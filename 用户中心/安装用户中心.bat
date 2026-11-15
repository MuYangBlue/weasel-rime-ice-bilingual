@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$root = [IO.Path]::GetFullPath('%ROOT%'); $exe = Join-Path $root 'dist\小狼毫用户中心\小狼毫用户中心.exe'; if (-not (Test-Path $exe)) { $exe = Join-Path $root '启动用户中心.bat' }; $shell = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $startup = [Environment]::GetFolderPath('Startup'); foreach ($dir in @($desktop, $startup)) { $link = Join-Path $dir '小狼毫用户中心.lnk'; $shortcut = $shell.CreateShortcut($link); $shortcut.TargetPath = $exe; $shortcut.WorkingDirectory = $root; $shortcut.Description = '小狼毫用户中心'; $shortcut.Save() }; Write-Host '已创建桌面快捷方式和开机启动项。'"
pause
endlocal
