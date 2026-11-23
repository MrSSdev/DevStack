# Generate simple original app artwork on the Windows build runner.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = Split-Path -Parent $PSScriptRoot
foreach ($folder in @('Assets','ProviderAssets')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $folder) | Out-Null
}
function New-Asset {
    param([string]$File, [int]$W, [int]$H, [string]$Title, [string]$Color)
    $bitmap = [System.Drawing.Bitmap]::new($W, $H)
    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    $accent = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($Color))
    $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $font = [System.Drawing.Font]::new('Segoe UI', [Math]::Max(12, [Math]::Min($H/4, $W/[Math]::Max(3,$Title.Length))), [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $format = [System.Drawing.StringFormat]::new()
    try {
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.Clear([System.Drawing.Color]::FromArgb(13,18,28))
        $g.FillRectangle($accent, 0, $H-[Math]::Max(3,$H/24), $W, [Math]::Max(3,$H/24))
        $format.Alignment = [System.Drawing.StringAlignment]::Center
        $format.LineAlignment = [System.Drawing.StringAlignment]::Center
        $g.DrawString($Title, $font, $white, [System.Drawing.RectangleF]::new(0,0,$W,$H), $format)
        $bitmap.Save((Join-Path $root $File), [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $format.Dispose(); $font.Dispose(); $white.Dispose(); $accent.Dispose()
        $g.Dispose(); $bitmap.Dispose()
    }
}
@(
    @('Assets/StoreLogo.png',80,80,'DS','#E1403E'),
    @('Assets/Square44x44Logo.png',88,88,'DS','#E1403E'),
    @('Assets/Square150x150Logo.png',300,300,'DS','#E1403E'),
    @('Assets/Wide310x150Logo.png',620,300,'DEVSTACK','#E1403E'),
    @('Assets/SplashScreen.png',620,300,'DEVSTACK','#E1403E'),
    @('ProviderAssets/WSL_Icon.png',200,200,'WSL','#2EC9A5'),
    @('ProviderAssets/WSL_Screenshot.png',480,340,'WSL','#2EC9A5'),
    @('ProviderAssets/DevStack_Icon.png',200,200,'DEV','#E1403E'),
    @('ProviderAssets/DevStack_Screenshot.png',480,340,'DEVSTACK','#E1403E')
) | ForEach-Object { New-Asset -File $_[0] -W $_[1] -H $_[2] -Title $_[3] -Color $_[4] }


# Small transparent glyphs are embedded as data:image/png in the native
# Adaptive Cards. No external assets or web requests are required for icons.
function New-UiGlyph {
    param([string]$File, [string]$Kind, [string]$Color)

    $bmp = [System.Drawing.Bitmap]::new(48, 48, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $ink = [System.Drawing.ColorTranslator]::FromHtml($Color)
    $pen = [System.Drawing.Pen]::new($ink, 3.5)
    $fill = [System.Drawing.SolidBrush]::new($ink)
    try {
        $g.Clear([System.Drawing.Color]::Transparent)
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round

        switch ($Kind) {
            'power' {
                $g.DrawArc($pen, 8, 8, 32, 32, -56, 292)
                $g.DrawLine($pen, 24, 5, 24, 23)
            }
            'dev' {
                $g.FillRectangle($fill, 9, 9, 13, 13)
                $g.FillRectangle($fill, 26, 9, 13, 13)
                $g.FillRectangle($fill, 9, 26, 13, 13)
                $g.FillRectangle($fill, 26, 26, 13, 13)
            }
            'globe' {
                $g.DrawEllipse($pen, 7, 7, 34, 34)
                $g.DrawLine($pen, 7, 24, 41, 24)
                $g.DrawArc($pen, 18, 7, 12, 34, 88, 184)
                $g.DrawArc($pen, 18, 7, 12, 34, -88, 184)
            }
            'code' {
                $g.DrawLine($pen, 16, 15, 8, 24)
                $g.DrawLine($pen, 8, 24, 16, 33)
                $g.DrawLine($pen, 32, 15, 40, 24)
                $g.DrawLine($pen, 40, 24, 32, 33)
                $g.DrawLine($pen, 28, 10, 20, 38)
            }
            'database' {
                $g.DrawEllipse($pen, 7, 9, 34, 13)
                $g.DrawArc($pen, 7, 15, 34, 13, 0, 180)
                $g.DrawArc($pen, 7, 25, 34, 13, 0, 180)
                $g.DrawLine($pen, 7, 15, 7, 32)
                $g.DrawLine($pen, 41, 15, 41, 32)
                $g.DrawArc($pen, 7, 25, 34, 13, 0, 180)
            }
            'layers' {
                $top = [System.Drawing.Point[]]@(
                    [System.Drawing.Point]::new(24,8), [System.Drawing.Point]::new(41,18),
                    [System.Drawing.Point]::new(24,28), [System.Drawing.Point]::new(7,18))
                $g.DrawPolygon($pen, $top)
                $g.DrawLine($pen, 7, 26, 24, 36)
                $g.DrawLine($pen, 24, 36, 41, 26)
                $g.DrawLine($pen, 7, 33, 24, 43)
                $g.DrawLine($pen, 24, 43, 41, 33)
            }
        }

        $bmp.Save((Join-Path $root $File), [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $fill.Dispose(); $pen.Dispose(); $g.Dispose(); $bmp.Dispose()
    }
}
@(
    @('ProviderAssets/UI_WSL.png','power','#2EC9A5'),
    @('ProviderAssets/UI_DevStack.png','dev','#E1403E'),
    @('ProviderAssets/UI_Nginx.png','globe','#5DADB9'),
    @('ProviderAssets/UI_PHP.png','code','#8F9ED1'),
    @('ProviderAssets/UI_MariaDB.png','database','#C7A578'),
    @('ProviderAssets/UI_Redis.png','layers','#D17D84')
) | ForEach-Object { New-UiGlyph -File $_[0] -Kind $_[1] -Color $_[2] }
