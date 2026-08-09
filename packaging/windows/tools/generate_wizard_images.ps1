# Generates the branded wizard images for the Inno Setup installer.
#
# Samples the dominant (non-transparent) color from the app icon and renders:
#   * assets\wizard-welcome.bmp        - left pane image (2x of 202x386)
#   * assets\wizard-small.bmp          - upper-right corner image (2x of 55x55)
#
# Output is BMP-only for maximum compatibility with every Inno Setup 6.x.
# Run from the repository root:
#   pwsh packaging\windows\tools\generate_wizard_images.ps1

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing

$repoRoot = Get-Location
$logoPath = Join-Path $repoRoot "src\assets\icon_windows.png"
if (-not (Test-Path $logoPath)) {
    $logoPath = Join-Path $repoRoot "src\assets\Unscreen_logo.png"
}
$outDir = Join-Path $repoRoot "packaging\windows\assets"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

function Get-LogoColor {
    param([string]$Path)
    $bmp = [System.Drawing.Bitmap]::new($Path)
    try {
        [long]$r = 0; [long]$g = 0; [long]$b = 0; [long]$count = 0
        $lock = $bmp.LockBits(
            [System.Drawing.Rectangle]::new(0, 0, $bmp.Width, $bmp.Height),
            [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
            [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        try {
            $bytes = New-Object byte[] ($lock.Stride * $lock.Height)
            [System.Runtime.InteropServices.Marshal]::Copy($lock.Scan0, $bytes, 0, $bytes.Length)
            for ($y = 0; $y -lt $bmp.Height; $y++) {
                for ($x = 0; $x -lt $bmp.Width; $x++) {
                    $i = $y * $lock.Stride + $x * 4
                    if ($bytes[$i + 3] -gt 128) {
                        $r += $bytes[$i + 2]; $g += $bytes[$i + 1]; $b += $bytes[$i]
                        $count++
                    }
                }
            }
        } finally {
            $bmp.UnlockBits($lock)
        }
        if ($count -eq 0) { return [System.Drawing.Color]::FromArgb(255, 90, 90, 110) }
        return [System.Drawing.Color]::FromArgb(
            255,
            [Math]::Clamp([int]($r / $count), 0, 255),
            [Math]::Clamp([int]($g / $count), 0, 255),
            [Math]::Clamp([int]($b / $count), 0, 255))
    } finally {
        $bmp.Dispose()
    }
}

function New-Gradient {
    param(
        [int]$Width,
        [int]$Height,
        [System.Drawing.Color]$Top,
        [System.Drawing.Color]$Bottom)
    $bmp = [System.Drawing.Bitmap]::new($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $brush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
            [System.Drawing.Rectangle]::new(0, 0, $Width, $Height), $Top, $Bottom, 90.0)
        $g.FillRectangle($brush, 0, 0, $Width, $Height)
        $brush.Dispose()
    } finally {
        $g.Dispose()
    }
    return $bmp
}

function Add-PastedImage {
    param(
        [System.Drawing.Bitmap]$Canvas,
        [string]$SrcPath,
        [float]$Scale,
        [int]$CenterY)
    $src = [System.Drawing.Bitmap]::new($SrcPath)
    try {
        $w = [int]([Math]::Round($src.Width * $Scale))
        $h = [int]([Math]::Round($src.Height * $Scale))
        $x = [int](($Canvas.Width - $w) / 2)
        $y = $CenterY - [int]($h / 2)
        $g = [System.Drawing.Graphics]::FromImage($Canvas)
        try {
            $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $g.DrawImage($src, $x, $y, $w, $h)
        } finally {
            $g.Dispose()
        }
    } finally {
        $src.Dispose()
    }
}

function Add-Text {
    param(
        [System.Drawing.Bitmap]$Canvas,
        [string]$Text,
        [float]$SizePx,
        [int]$CenterY,
        [System.Drawing.Color]$Color)
    $family = [System.Drawing.FontFamily]::new("Segoe UI")
    $font = [System.Drawing.Font]::new($family, $SizePx, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    try {
        $g = [System.Drawing.Graphics]::FromImage($Canvas)
        try {
            $brush = [System.Drawing.SolidBrush]::new($Color)
            $sf = [System.Drawing.StringFormat]::new()
            $sf.Alignment = [System.Drawing.StringAlignment]::Center
            $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
            $rect = [System.Drawing.RectangleF]::new(0.0, [float]($CenterY - $SizePx), [float]$Canvas.Width, [float]($SizePx * 3))
            $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
            $g.DrawString($Text, $font, $brush, $rect, $sf)
            $brush.Dispose()
            $sf.Dispose()
        } finally {
            $g.Dispose()
        }
    } finally {
        $font.Dispose()
    }
}

function Save-Bmp {
    param([System.Drawing.Bitmap]$Bmp, [string]$Path)
    $bmp2 = [System.Drawing.Bitmap]::new($Bmp.Width, $Bmp.Height, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp2)
    $g.DrawImage($Bmp, 0, 0, $Bmp.Width, $Bmp.Height)
    $g.Dispose()
    $bmp2.Save($Path, [System.Drawing.Imaging.ImageFormat]::Bmp)
    $bmp2.Dispose()
}

$brand = Get-LogoColor -Path $logoPath
$nearDark = [System.Drawing.Color]::FromArgb(255, 24, 24, 32)

$main = New-Gradient -Width 404 -Height 772 -Top $brand -Bottom $nearDark
Add-PastedImage -Canvas $main -SrcPath $logoPath -Scale 0.28 -CenterY ([int](772 * 0.42))
Add-Text -Canvas $main -Text "Unscreen" -SizePx 44 -CenterY ([int](772 * 0.70)) -Color ([System.Drawing.Color]::White)
Save-Bmp -Bmp $main -Path (Join-Path $outDir "wizard-welcome.bmp")
$main.Dispose()

$small = New-Gradient -Width 110 -Height 110 -Top $brand -Bottom $nearDark
Add-PastedImage -Canvas $small -SrcPath $logoPath -Scale 0.55 -CenterY 55
Save-Bmp -Bmp $small -Path (Join-Path $outDir "wizard-small.bmp")
$small.Dispose()

Write-Output "Generated:"
Get-ChildItem $outDir | Select-Object Name, Length