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
