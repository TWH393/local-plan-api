"""Allowlisted release packaging. Never includes local runtime credentials."""
from pathlib import Path
import argparse
import shutil
import zipfile

root=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description='Prepare the fixed application folder; ZIP is optional.')
parser.add_argument('--zip',action='store_true',help='Export a single fixed-name portable ZIP for sharing.')
args=parser.parse_args()
output=root.parent/'output/local-plan-api'
output.mkdir(parents=True,exist_ok=True)

def archive(path,files,prefix,base):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for file in files:
            relative=file.relative_to(base)
            if any(p in {'.venv','__pycache__','.git'} for p in relative.parts):raise RuntimeError('Unexpected runtime directory')
            if file.suffix in {'.bin','.sqlite3','.log'}:raise RuntimeError('Unexpected runtime data')
            z.write(file,str(Path(prefix)/relative))
# Source history belongs in Git; do not generate a source ZIP for each version.
portable=root/'dist/LocalPlanAPI'
for name in ('README.md','LICENSE'):shutil.copy2(root/name,portable/name)
if args.zip:
    archive(output/'LocalPlanAPI-Windows.zip',[p for p in portable.rglob('*') if p.is_file()],'LocalPlanAPI',portable)
print('Ready: '+str(portable/'LocalPlanAPI.exe'))
print('ZIP export: '+('LocalPlanAPI-Windows.zip' if args.zip else 'disabled; source versions are managed with Git'))
