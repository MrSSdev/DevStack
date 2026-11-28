"""Experimental HTTP ingress for disposable isolated runtime fixtures.

Not connected to the DevStack daemon. All mount sources must be stable,
trusted fixture paths; these checks are not a production authorization API.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from project_runtime import RuntimePlan, checked_path, fixture_launch_argv
from project_mounts import PinnedProject

NGINX = '/usr/sbin/nginx'
HTTP_SOCKET = '/run/project-http/http.sock'
HOST = re.compile(r'[a-z][a-z0-9-]{0,40}\.fixture\.test\Z')


def fixture_path(path: Path, *, directory: bool) -> str:
    value = checked_path(path, directory=directory)
    if not path.is_relative_to('/var/lib') or path == Path('/var/lib'):
        raise ValueError('Not a fixture path')
    return value


def preamble(work: str) -> str:
    return f'''daemon off;
master_process off;
worker_processes 1;
pid {work}/nginx.pid;
error_log {work}/error.log notice;
events {{ worker_connections 64; }}
http {{
    access_log off;
    server_tokens off;
    client_body_temp_path {work}/client;
    proxy_temp_path {work}/proxy;
    fastcgi_temp_path {work}/fastcgi;
    uwsgi_temp_path {work}/uwsgi;
    scgi_temp_path {work}/scgi;
'''


@dataclass(frozen=True)
class ProjectHttp:
    runtime: RuntimePlan
    config: Path
    sockets: Path

    def sources(self) -> tuple[str, str, str | None, str]:
        source_path = checked_path if self.runtime.registration is not None else fixture_path
        project = source_path(self.runtime.project, directory=True)
        public = source_path(self.runtime.project / 'public', directory=True)
        storage = self.runtime.project / 'storage/app/public'
        published = (source_path(storage, directory=True)
                     if storage.exists() or storage.is_symlink() else None)
        fpm = source_path(self.runtime.sockets, directory=True)
        return project, public, published, fpm

    def launch_argv(self) -> list[str]:
        return self.runtime.component_argv('http')

    def argv(self, mounts: PinnedProject) -> list[str]:
        if mounts.path != self.runtime.project:
            raise ValueError('Project mount authorization mismatch')
        source_path = checked_path if self.runtime.registration is not None else fixture_path
        fpm = source_path(self.runtime.sockets, directory=True)
        config = source_path(self.config, directory=False)
        sockets = source_path(self.sockets, directory=True)
        if self.sockets == self.runtime.sockets:
            raise ValueError('HTTP and PHP must use distinct socket directories')
        args = [
            '/usr/bin/bwrap', '--unshare-user', '--unshare-pid',
            '--unshare-net', '--unshare-ipc', '--unshare-uts',
            '--disable-userns', '--die-with-parent', '--new-session',
            '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--dir', '/home', '--dir', '/mnt', '--dir', '/run',
            '--ro-bind', '/etc/nginx/mime.types', '/etc/nginx/mime.types',
            '--ro-bind', config, '/run/project-http-config/nginx.conf',
            '--ro-bind', fpm, '/run/project-fpm',
            '--bind', sockets, '/run/project-http',
        ]
        if Path('/usr/lib/wsl').is_dir():
            args += ['--tmpfs', '/usr/lib/wsl']
        args += mounts.bind('public')
        args += mounts.bind('storage/app/public', optional=True)
        return args + [
            '--chdir', '/tmp', '--clearenv',
            '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
            '--', NGINX, '-p', '/tmp/', '-c', '/run/project-http-config/nginx.conf',
        ]

    def nginx_config(self) -> str:
        _, public, storage, _ = self.sources()
        # The conventional Laravel public/storage symlink is not followed.
        # Publish exactly the approved directory through a fixed alias.
        published = (f'alias {storage}/;' if storage else 'return 404;')
        return preamble('/run/project-http') + f'''
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    map $http_x_forwarded_proto $project_https {{
        default "";
        https on;
    }}
    server {{
        listen unix:{HTTP_SOCKET};
        root {public};
        index index.php index.html;
        autoindex off;
        disable_symlinks on;
        location = /index.php {{
            fastcgi_pass unix:/run/project-fpm/php.sock;
            fastcgi_ignore_headers X-Accel-Redirect;
            fastcgi_param SCRIPT_FILENAME {public}/index.php;
            fastcgi_param SCRIPT_NAME /index.php;
            fastcgi_param DOCUMENT_ROOT {public};
            fastcgi_param QUERY_STRING $query_string;
            fastcgi_param REQUEST_METHOD $request_method;
            fastcgi_param CONTENT_TYPE $content_type;
            fastcgi_param CONTENT_LENGTH $content_length;
            fastcgi_param REQUEST_URI $request_uri;
            fastcgi_param SERVER_PROTOCOL $server_protocol;
            fastcgi_param SERVER_NAME $host;
            fastcgi_param SERVER_PORT $http_x_forwarded_port;
            fastcgi_param REMOTE_ADDR $http_x_forwarded_for;
            fastcgi_param HTTPS $project_https if_not_empty;
            fastcgi_param REDIRECT_STATUS 200;
            fastcgi_param HTTP_PROXY "";
        }}
        location ~ /\\. {{ return 404; }}
        location ~* \\.(?:php[0-9]*|phtml|phar)(?:/|$) {{ return 404; }}
        location /storage/ {{ {published} }}
        location /build/ {{ try_files $uri =404; }}
        location / {{ try_files $uri $uri/ /index.php?$query_string; }}
    }}
}}
'''


def gateway_config(work: Path, cert: Path, key: Path, port: int,
                   routes: dict[str, ProjectHttp]) -> str:
    """Trusted ingress owns fixed routes, never project document roots."""
    workdir = fixture_path(work, directory=True)
    certificate = fixture_path(cert, directory=False)
    private_key = fixture_path(key, directory=False)
    if type(port) is not int or not 1024 <= port <= 65535 or not routes:
        raise ValueError('Invalid fixture listener')
    text = preamble(workdir)
    text += f'''
    ssl_certificate {certificate};
    ssl_certificate_key {private_key};
    ssl_protocols TLSv1.2 TLSv1.3;
    server {{
        listen 127.0.0.1:{port} ssl default_server;
        server_name _;
        return 421;
    }}
'''
    used = set()
    for host, plan in routes.items():
        if not HOST.fullmatch(host):
            raise ValueError('Invalid fixture host')
        sock = fixture_path(plan.sockets, directory=True) + '/http.sock'
        if sock in used:
            raise ValueError('Duplicate fixture upstream')
        used.add(sock)
        text += f'''
    server {{
        listen 127.0.0.1:{port} ssl;
        server_name {host};
        location / {{
            proxy_pass http://unix:{sock};
            proxy_http_version 1.1;
            proxy_ignore_headers X-Accel-Redirect;
            proxy_set_header Host $host;
            proxy_set_header Connection "";
            proxy_set_header Forwarded "";
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Port $server_port;
            proxy_set_header X-Forwarded-Proto $scheme;
        }}
    }}
'''
    return text + '}\n'
