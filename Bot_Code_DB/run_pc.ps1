$log = "C:\Users\Work - Study (Grind)\Desktop\Fast_Foreward_Bots\Bot_Code_DB\pc_bot.log"
"=== BOT START at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Encoding utf8
python -u main.py *>&1 | Out-File $log -Encoding utf8 -Append
