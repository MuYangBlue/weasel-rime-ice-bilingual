@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$desktop = [Environment]::GetFolderPath('Desktop'); $startup = [Environment]::GetFolderPath('Startup'); foreach ($dir in @($desktop, $startup)) { $link = Join-Path $dir '小狼毫用户中心.lnk'; if (Test-Path $link) { Remove-Item $link -Force } }; Write-Host '已删除用户中心快捷方式和开机启动项。'"
pause
endlocal
