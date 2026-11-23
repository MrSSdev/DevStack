# Original line icons, drawn at high resolution for sharp PNG assets.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = Split-Path -Parent $PSScriptRoot
foreach ($folder in @('Assets','ProviderAssets')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $folder) | Out-Null
}
function RoundRect([single]$x,[single]$y,[single]$w,[single]$h,[single]$r) {
    $p = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $d = $r * 2
    $p.AddArc($x,$y,$d,$d,180,90)
    $p.AddArc($x+$w-$d,$y,$d,$d,270,90)
    $p.AddArc($x+$w-$d,$y+$h-$d,$d,$d,0,90)
    $p.AddArc($x,$y+$h-$d,$d,$d,90,90)
    $p.CloseFigure()
    return $p
}
function New-Glyph([string]$Name,[string]$Kind,[string]$Color) {
    $bmp = [System.Drawing.Bitmap]::new(96,96,[System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $ink = [System.Drawing.ColorTranslator]::FromHtml($Color)
    $pen = [System.Drawing.Pen]::new($ink,2.1)
    $fill = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(22,$ink))
    $frame = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(45,$ink),0.7)
    $tile = RoundRect 1 1 46 46 12
    try {
        $g.Clear([System.Drawing.Color]::Transparent)
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.ScaleTransform(2,2)
        $g.FillPath($fill,$tile); $g.DrawPath($frame,$tile)
        $pen.StartCap = 'Round'; $pen.EndCap = 'Round'; $pen.LineJoin = 'Round'
        switch ($Kind) {
            'terminal' {
                $box = RoundRect 10 13 28 23 4
                try { $g.DrawPath($pen,$box) } finally { $box.Dispose() }
                $g.DrawLine($pen,16,20,21,24); $g.DrawLine($pen,21,24,16,28)
                $g.DrawLine($pen,25,29,32,29)
            }
            'blocks' {
                foreach ($b in @(@(12,12),@(26,12),@(12,26),@(26,26))) {
                    $box = RoundRect $b[0] $b[1] 10 10 2
                    try { $g.DrawPath($pen,$box) } finally { $box.Dispose() }
                }
            }
            'globe' {
                $g.DrawEllipse($pen,12,12,24,24)
                $g.DrawEllipse($pen,19,12,10,24)
                $g.DrawLine($pen,12,24,36,24)
            }
            'code' {
                $g.DrawLine($pen,17,17,10,24); $g.DrawLine($pen,10,24,17,31)
                $g.DrawLine($pen,31,17,38,24); $g.DrawLine($pen,38,24,31,31)
                $g.DrawLine($pen,27,14,21,34)
            }
            'database' {
                $g.DrawEllipse($pen,12,12,24,8)
                $g.DrawLine($pen,12,16,12,32); $g.DrawLine($pen,36,16,36,32)
                $g.DrawArc($pen,12,20,24,8,0,180)
                $g.DrawArc($pen,12,28,24,8,0,180)
            }
            'layers' {
                $pts = [System.Drawing.PointF[]]@(
                    [System.Drawing.PointF]::new(24,12),[System.Drawing.PointF]::new(37,20),
                    [System.Drawing.PointF]::new(24,28),[System.Drawing.PointF]::new(11,20))
                $g.DrawPolygon($pen,$pts)
                $g.DrawLine($pen,11,27,24,35); $g.DrawLine($pen,24,35,37,27)
            }
        }
        $bmp.Save((Join-Path $root "ProviderAssets/UI_$Name.png"),[System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $tile.Dispose(); $frame.Dispose(); $fill.Dispose(); $pen.Dispose(); $g.Dispose(); $bmp.Dispose() }
}
@(
    @('WSL','terminal','#6AA893'), @('DevStack','blocks','#8997AA'),
    @('Nginx','globe','#6AA893'), @('PHP','code','#9696BA'),
    @('MariaDB','database','#B29A79'), @('Redis','layers','#B58285')
) | ForEach-Object { New-Glyph $_[0] $_[1] $_[2] }

function Save-Art([string]$File,[int]$Width,[int]$Height,[string]$Glyph,[string]$Title='') {
    $bmp = [System.Drawing.Bitmap]::new($Width,$Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $img = [System.Drawing.Image]::FromFile((Join-Path $root "ProviderAssets/UI_$Glyph.png"))
    $font = [System.Drawing.Font]::new('Segoe UI',22,[System.Drawing.FontStyle]::Regular,[System.Drawing.GraphicsUnit]::Pixel)
    $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(232,234,238))
    try {
        $g.Clear([System.Drawing.Color]::FromArgb(32,35,41))
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $side = [Math]::Min($Width,$Height)*0.6
        if ($Title) {
            $g.DrawImage($img,24,24,48,48)
            $g.DrawString($Title,$font,$white,88,36)
        } else { $g.DrawImage($img,($Width-$side)/2,($Height-$side)/2,$side,$side) }
        $bmp.Save((Join-Path $root $File),[System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $white.Dispose(); $font.Dispose(); $img.Dispose(); $g.Dispose(); $bmp.Dispose() }
}
Save-Art 'Assets/StoreLogo.png' 80 80 'DevStack'
Save-Art 'Assets/Square44x44Logo.png' 88 88 'DevStack'
Save-Art 'Assets/Square150x150Logo.png' 300 300 'DevStack'
Save-Art 'Assets/Wide310x150Logo.png' 620 300 'DevStack' 'DevStack'
Save-Art 'Assets/SplashScreen.png' 620 300 'DevStack' 'DevStack'
Copy-Item (Join-Path $root 'ProviderAssets/UI_WSL.png') (Join-Path $root 'ProviderAssets/WSL_Icon.png') -Force
Copy-Item (Join-Path $root 'ProviderAssets/UI_DevStack.png') (Join-Path $root 'ProviderAssets/DevStack_Icon.png') -Force
Save-Art 'ProviderAssets/WSL_Screenshot.png' 300 304 'WSL' 'Ubuntu'
Save-Art 'ProviderAssets/DevStack_Screenshot.png' 300 304 'DevStack' 'DevStack'
