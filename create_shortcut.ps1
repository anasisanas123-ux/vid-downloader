# Creates a "Vid Downloader" shortcut on the Desktop that starts the app (no console) and opens the browser.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')   # handles OneDrive-redirected desktops
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Vid Downloader.lnk'))
$lnk.TargetPath = Join-Path $root '.venv\Scripts\pythonw.exe'
$lnk.Arguments = '"' + (Join-Path $root 'launch.pyw') + '"'
$lnk.WorkingDirectory = $root
$icon = Join-Path $root 'static\icon.ico'
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Description = 'Download and cut videos'
$lnk.Save()
Write-Host "Shortcut created: $(Join-Path $desktop 'Vid Downloader.lnk')"
