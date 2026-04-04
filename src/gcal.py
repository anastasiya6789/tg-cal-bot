from googleapiclient.discovery import build
from oauth import get_credentials
from datetime import datetime, timedelta
import pytz, os, re, logging

logger = logging.getLogger(__name__)
TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

TYPE_TAG_RE = re.compile(r'<!--\s*TG_TYPE:\s*(meeting|event)\s*-->', re.I)
TASK_KEYS = ['дедлайн','deadline','задача','сделать','подготовить','лаба','реферат','отчет','дз']
TYPE_EMOJI = {'meeting':'📅 Встречи','task':'✅ Задачи','event':'🎯 Мероприятия'}
TYPE_ORDER = ['meeting','task','event']
AUTO_DESC = ['изменения в названии, описании','изменения в описании','изменения в названии','изменения в местоположении','changes to title, description','changes to description','changes to title','changes to location','конференция: присоединиться через google meet','video call: join with google meet','']

def to_iso(dt):
    if dt.tzinfo is None: dt = tz.localize(dt)
    return dt.isoformat()

def parse_dt(d):
    if not d: return None
    s = d.get('dateTime') or d.get('date')
    if not s: return None
    try:
        if 'T' in s:
            if s.endswith('Z'):
                dt = datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(tz)
            else:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None: dt = tz.localize(dt)
        else:
            dt = tz.localize(datetime.strptime(s,"%Y-%m-%d").replace(hour=12))
        return dt
    except: return None

def fmt_range(sd, ed, disp=None):
    if disp: return disp
    st, et = parse_dt(sd), parse_dt(ed)
    if not st: return "весь день"
    if 'date' in sd and 'dateTime' not in sd: return "весь день"
    ss = st.strftime("%H:%M")
    if et:
        es = et.strftime("%H:%M")
        return ss if ss==es else f"{ss}-{es}"
    return ss

def fmt_evt(e):
    tr = fmt_range(e.get('start'), e.get('end'), e.get('_disp'))
    tl = e.get('summary','Без названия')
    lc = f" 📍{e.get('location')}" if e.get('location') else ""
    ds = e.get('description','')
    for ad in AUTO_DESC:
        if ds.lower().strip()==ad: ds=''
        else: ds = TYPE_TAG_RE.sub('',ds).strip()
    ds = f" 💬 {ds[:30]}..." if len(ds)>30 else (f" 💬 {ds}" if ds else "")
    return f"⏰ {tr} — {tl}{lc}{ds}"

def detect_typ(e):
    if e.get('_task'): return 'task'
    dsc = (e.get('description') or '').lower()
    ttl = (e.get('summary') or '').lower()
    m = TYPE_TAG_RE.search(e.get('description') or '')
    if m: return m.group(1).lower()
    if e.get('attendees'): return 'meeting'
    if any(k in f"{ttl} {dsc}" for k in TASK_KEYS): return 'task'
    return 'event'

async def create_evt(uid, data):
    cr = await get_credentials(uid)
    if not cr: return False,"❌ Сначала подключи Google командой /connect"
    
    if data.get('type')=='task':
        ts = build('tasks','v1',credentials=cr)
        due = datetime.fromisoformat(data['start'])
        if due.tzinfo is None: due = tz.localize(due)
        # Формат для Tasks API: 2026-04-06T16:05:00+03:00
        due_str = due.strftime("%Y-%m-%dT%H:%M:%S%z")
        due_str = due_str[:-2]+':'+due_str[-2:] if len(due_str)==25 and due_str[-5]in'+-' else due_str
        try:
            ts.tasks().insert(tasklist='@default',body={'title':data['title'],'notes':data.get('description',''),'due':due_str,'status':'needsAction'}).execute()
            return True,"✅ Задача создана!"
        except Exception as ex:
            logger.error(f"Tasks err: {ex}")
            return False,f"❌ Ошибка: {ex}"
    
    svc = build('calendar','v3',credentials=cr)
    st = datetime.fromisoformat(data['start'])
    en = datetime.fromisoformat(data['end'])
    if st.tzinfo is None: st = tz.localize(st)
    if en.tzinfo is None: en = tz.localize(en)
    desc = (data.get('description') or '').strip()
    tag = f"<!-- TG_TYPE:{data['type']} -->"
    desc = f"{desc}\n{tag}" if desc else tag
    body = {'summary':data['title'],'description':desc,'location':data.get('location',''),'start':{'dateTime':to_iso(st),'timeZone':TZ_NAME},'end':{'dateTime':to_iso(en),'timeZone':TZ_NAME},'colorId':data.get('color'),'reminders':{'useDefault':False,'overrides':[{'method':'popup','minutes':15},{'method':'email','minutes':60}]}}
    try:
        ev = svc.events().insert(calendarId='primary',body=body).execute()
        return True,f"✅ Создано!\n{ev.get('htmlLink','')}"
    except Exception as ex:
        logger.error(f"Cal err: {ex}")
        return False,f"❌ Ошибка: {ex}"

async def get_sched(uid, period="day", target=None, off=0, lim=20):
    cr = await get_credentials(uid)
    if not cr: return False,"❌ Сначала подключи Google",False,None
    
    base = datetime.strptime(target,"%Y-%m-%d") if target else datetime.now(tz)
    base = tz.localize(base.replace(hour=12,minute=0,second=0))
    
    if period=="day":
        st = base.replace(hour=0,minute=0,second=0,microsecond=0)
        en = base.replace(hour=23,minute=59,second=59,microsecond=0)
    elif period=="week":
        st = (base-timedelta(days=base.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
        en = st+timedelta(days=6,hours=23,minutes=59,seconds=59,microseconds=0)
    elif period=="month":
        st = base.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        nm, ny = (1,base.year+1) if base.month==12 else (base.month+1,base.year)
        en = st.replace(month=nm,day=1,year=ny,microsecond=0)-timedelta(seconds=1)
    else: return False,"❌ Неизвестный период",False,None
    
    items = []
    
    # Calendar
    try:
        cal = build('calendar','v3',credentials=cr)
        for e in cal.events().list(calendarId='primary',timeMin=to_iso(st),timeMax=to_iso(en),singleEvents=True,orderBy='startTime').execute().get('items',[]):
            sdt = parse_dt(e.get('start'))
            items.append({'summary':e.get('summary',''),'description':e.get('description',''),'location':e.get('location',''),'start':e.get('start',{}),'end':e.get('end',{}),'_task':False,'_disp':None,'_sdt':sdt,'_dk':sdt.strftime("%Y-%m-%d") if sdt else None})
    except Exception as ex: logger.error(f"Cal API: {ex}")
    
    # Tasks
    try:
        ts = build('tasks','v1',credentials=cr)
        tmin = st.strftime("%Y-%m-%dT00:00:00%z").replace('+0000','+00:00')
        tmax = en.strftime("%Y-%m-%dT23:59:59%z").replace('+0000','+00:00')
        for t in ts.tasks().list(tasklist='@default',dueMin=tmin,dueMax=tmax,showCompleted=False,showHidden=False).execute().get('items',[]):
            due = t.get('due')
            if not due: continue
            # Парсим время: берём локальное время из строки
            if 'T' in due:
                # Формат: 2026-04-06T16:05:00+03:00
                parts = due.replace('Z','+00:00').split('+')
                dt_part = parts[0]
                tz_part = '+'+parts[1] if len(parts)>1 and parts[1] else '+00:00'
                # Парсим дату и время
                date_t = dt_part.split('T')
                if len(date_t)==2:
                    date_p, time_p = date_t[0], date_t[1][:5]
                    disp = time_p
                    sdt = tz.localize(datetime.strptime(f"{date_p} {time_p}","%Y-%m-%d %H:%M"))
                else:
                    disp = "до конца дня"
                    sdt = tz.localize(datetime.strptime(dt_part,"%Y-%m-%d").replace(hour=23,minute=59))
            else:
                disp = "до конца дня"
                sdt = tz.localize(datetime.strptime(due,"%Y-%m-%d").replace(hour=23,minute=59))
            
            if sdt < st or sdt > en: continue
            items.append({'summary':t['title'],'description':t.get('notes',''),'location':'','start':{'dateTime':due},'end':{'dateTime':due},'_task':True,'_disp':disp,'_sdt':sdt,'_dk':sdt.strftime("%Y-%m-%d")})
    except Exception as ex: logger.error(f"Tasks API: {ex}")
    
    items.sort(key=lambda x: x.get('_sdt') or datetime.max.replace(tzinfo=tz))
    for it in items: it['_typ'] = detect_typ(it)
    
    grp = {}
    for it in items:
        dk = it.get('_dk')
        if not dk: continue
        tt = it.get('_typ','event')
        grp.setdefault(tt,{}).setdefault(dk,[]).append(it)
    
    flat = []
    for typ in TYPE_ORDER:
        if typ in grp:
            for dk in sorted(grp[typ].keys()): flat.extend(grp[typ][dk])
    
    pag = flat[off:off+lim]
    more = len(flat) > off+lim
    
    if not pag:
        txt = "📭 Нет событий и задач на этот период."
    else:
        plab = {"day":"день","week":"неделю","month":"месяц"}[period]
        dr = f"{st.strftime('%d.%m.%Y')}–{en.strftime('%d.%m.%Y')}" if period!="day" else st.strftime("%d.%m.%Y")
        txt = f"📋 Расписание на {plab} ({dr}):\n\n"
        dgrp = {}
        for it in pag:
            dk = it.get('_dk')
            if not dk: continue
            dgrp.setdefault(it.get('_typ','event'),{}).setdefault(dk,[]).append(it)
        for typ in TYPE_ORDER:
            if typ in dgrp:
                txt += f"{TYPE_EMOJI[typ]}:\n"
                for dk in sorted(dgrp[typ].keys()):
                    txt += f"🗓 {datetime.strptime(dk,'%Y-%m-%d').strftime('%d.%m.%Y')}\n"
                    for it in dgrp[typ][dk]: txt += fmt_evt(it)+"\n"
                    txt += "\n"
    
    return True,txt.strip(),more,{"start":st,"end":en}