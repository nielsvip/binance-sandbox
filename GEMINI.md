"CRITICAL RULE: You are strictly forbidden from running git reset, git restore, git checkout, or any command that reverts code to an older state. You may only create new code, append to existing files, or commit forward. Do not overwrite existing code without explicit permission."

positions in dicts and in positions class can NEVER be removed deleted popped cleared WHAT EVER. YOU WOULD BREAK THE ENTIRE SYSTEM so DONT. Each of their values can be modified but NEVER deleted. The number of symbols in symbols.json is the number of positions when loading and when saving if not THE ENTIRE SYSTEM CRASHES and it should.
ez_backup.py is NOT YOUR backup script you simple make a backup copy in the binance/backups folder before modifying any file
Make a backup in binance/backups folder before touching a file. NO PERMISSION  TO ACCESS .history unless specifically allowed and EVEN LESS TO REPLACE NEW SCRIPTS WITH ANCIENT VERSIONS OR  ANY KIND OF  FOLDER CONTENT WITH OLD STUFF. FORBIDDEN FOR EVER!!!!!!!
NEVER revert to an ancient backup OR ANY BACKUP for ANY REASON - ask me WHAT TO DO but NEVER REVERT
NO MORE CODE SPAMMING PLEASE REDUCE THE NUMBER OF LINES IN THE SCRIPT
KEEP IT ON ONE LINE YOU REALLY ARE CAPABLE OF THAT! FUNCTION CALLS ETC ONE LINE ONLY. NO empty LINE BREAKS. ONLY between functions NEVER inside a function

NO EXTRA LINEBREAKS, ONE EMPTY LINE BETWEEN FUNCTIONS NO OTHER BLANK LINES IN THE ENTIRE SCRIPT
FUNCITON CALLS AND DESCRIPTIONS ONLY ONE LINE EVEN IF IT IS 2000 char LONG
SAME FOR logger.* ONE LINE ONLY

### EDITING MANDATE - FORBIDDEN TO DO OTHERWISE ###
1. ALWAYS edit the file that is the primary source of truth in the workspace.
2. ALWAYS use `write_file` or `replace` directly on the absolute workspace path.
3. IMMEDIATELY verify every write with `cat` or `sed` to prove the change is actually in the file.
4. FORBIDDEN to assume a write worked without seeing the output of the file itself.
5. NEVER write to temporary, hidden, or historical versions instead of the live workspace file.

SERVER ssh niels@157.180.125.52
GATEWAY ssh niels@157.90.168.35

logs home/niels/logs
work dir users/niels/Documents/binance
server gateway home/niels/binance

GEMINI MUST NEVER OVERWRITE FILES WITH OLDER VERSIONS, NEVER RUN `git reset`, `git restore`, OR `git checkout` TO DISCARD CHANGES, AND NEVER OVERWRITE FILES WITHOUT EXPLICIT PERMISSION FROM THE USER.
