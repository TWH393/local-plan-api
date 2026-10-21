"""Conservative automatic model selection within a named stable model family."""
import re
import time

PATTERN=re.compile(r'^gpt-(\d+(?:\.\d+)*)-(luna|sol|astra|terra)$')

def identify(model):
    hit=PATTERN.fullmatch(model or '')
    if not hit:return None
    parts=tuple(int(p) for p in hit[1].split('.'))
    return hit[2],parts+(0,)*(5-len(parts))

def follow_latest(settings,models):
    current=settings.get('selected_model','');parsed=identify(current)
    if not settings.get('auto_follow') or not parsed:return False
    candidates=[(identify(m['id']),m['id']) for m in models]
    candidates=[(p[1],name) for p,name in candidates if p and p[0]==parsed[0] and p[1]>parsed[1]]
    if not candidates:return False
    _,chosen=max(candidates)
    settings['selected_model']=chosen
    settings['model_update']={'from':current,'to':chosen,'at':time.time()}
    return True
