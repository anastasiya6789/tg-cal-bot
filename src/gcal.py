from googleapiclient.discovery import build
from oauth import get_credentials
from datetime import datetime, timedelta
import pytz, os, re, logging

logger = logging.getLogger(__name__)
TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

TYPE_TAG_RE = re.compile(r'<!--\s*TG_TYPE:\s*(meeting|event|task)\s*-->', re.I)
TASK_KEYWORDS = ['дедлайн', 'deadline', 'задача', 'task', 'сделать', 'подготовить', 'лаба', 'реферат', 'отчет', 'дз']
TYPE_EMOJI = {'meeting':'📅 Встречи','task':'✅ Задачи','event':'🎯 Мероприятия'}
TYPE_ORDER = ['meeting', 'task', 'event']
AUTO_DESC = [
    'изменения в названии, описании','изменения в описании','изменения в названии','изменения в местоположении',
    'changes to title, description','changes to description','changes to title','changes to location',
    'конференция: присоединиться через google meet','video call: join with google meet',''
]

def to_iso(dt):
    if dt.tzinfo is None: dt = tz.localize(dt)
    return dt.isoformat()

def parse_dt(s):
    if not s: return None
    try:
        if 'T' in s:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(s, "%Y-%m-%d")
        if dt.tzinfo is None: dt = tz.localize(dt)
        return dt
    except: return None

def detect_type(e):
    if e.get('_is_native_task'): return 'task'
    desc = e.get('description') or ''
    m = TYPE_TAG_RE.search(desc)
    if m: return m.group(1).lower()
    if e.get('attendees'): return 'meeting'
    ttl = (e.get('summary') or '').lower()
    if any(kw in f"{ttl} {desc.lower()}" for kw in TASK_KEYWORDS):
        return 'task'
    return 'event'

def clean_description(desc):
    if not desc: return ''
    desc = TYPE_TAG_RE.sub('', desc).strip()
    desc_lower = desc.lower().strip()
    for ad in AUTO_DESC:
        ad_clean = ad.lower().strip()
        if desc_lower == ad_clean or desc_lower.startswith(ad_clean):
            return ''
    return desc

def fmt_evt(e):
    start_data = e.get('start', {})
    end_data = e.get('end', {})
    is_all_day = 'date' in start_data and 'dateTime' not in start_data
    
    if is_all_day:
        time_str = "весь день"
    else:
        dt_str = start_data.get('dateTime')
        if not dt_str:
            time_str = "весь день"
        else:
            t_start = dt_str[11:16]
            if t_start == "00:00":
                time_str = "весь день"
            else:
                end_dt_str = end_data.get('dateTime')
                if end_dt_str and 'T' in end_dt_str:
                    t_end = end_dt_str[11:16]
                    time_str = t_start if t_start == t_end else f"{t_start}-{t_end}"
                else:
                    time_str = t_start
            
    title = e.get('summary', 'Без названия')
    loc = f" 📍{e.get('location')}" if e.get('location') else ""
    desc = clean_description(e.get('description', ''))
    desc_short = f" 💬 {desc[:30]}..." if len(desc) > 30 else (f" 💬 {desc}" if desc else "")
    
    return f"⏰ {time_str} — {title}{loc}{desc_short}"

async def create_event(uid, data):
    cr = await get_credentials(uid)
    if not cr: return False, "❌ Сначала подключи Google командой /connect", None
    
    svc = build('calendar','v3',credentials=cr)
    st = datetime.fromisoformat(data['start'])
    en = datetime.fromisoformat(data['end'])
    if st.tzinfo is None: st = tz.localize(st)
    if en.tzinfo is None: en = tz.localize(en)
    
    desc = (data.get('description') or '').strip()
    tag = f"<!-- TG_TYPE:{data['type']} -->"
    desc = f"{desc}\n{tag}" if desc else tag
    
    body = {
        'summary':data['title'],
        'description':desc,
        'location':data.get('location',''),
        'start':{'dateTime':to_iso(st),'timeZone':TZ_NAME},
        'end':{'dateTime':to_iso(en),'timeZone':TZ_NAME},
        'colorId':data.get('color'),
        'reminders':{'useDefault':False,'overrides':[{'method':'popup','minutes':15},{'method':'email','minutes':60}]}
    }
    try:
        ev = svc.events().insert(calendarId='primary',body=body).execute()
        event_id = ev.get('id')
        return True, f"✅ Создано!\n{ev.get('htmlLink','')}", event_id
    except Exception as ex:
        logger.error(f"Cal err: {ex}")
        return False, f"❌ Ошибка: {ex}", None

async def update_event(uid, event_id, new_data):
    cr = await get_credentials(uid)
    if not cr: return False, "❌ Сначала подключи Google"
    
    svc = build('calendar','v3',credentials=cr)
    
    # ✅ Получаем текущее событие + обработка 404
    try:
        existing = svc.events().get(calendarId='primary', eventId=event_id).execute()
    except Exception as fetch_err:
        err_str = str(fetch_err).lower()
        if "404" in err_str or "notfound" in err_str:
            return False, "❌ Событие не найдено в календаре"
        logger.error(f"Failed to fetch event: {fetch_err}")
        return False, f"❌ Ошибка: {fetch_err}"
    
    # Обновляем только переданные поля
    if 'title' in new_data:
        existing['summary'] = new_data['title']
    
    if 'description' in new_data:
        old_desc = existing.get('description', '')
        tag_match = TYPE_TAG_RE.search(old_desc)
        tag = tag_match.group(0) if tag_match else ''
        new_desc = new_data['description'].strip()
        existing['description'] = f"{new_desc}\n{tag}" if new_desc else tag
    
    if 'location' in new_data:
        existing['location'] = new_data['location'] or ''
    
    if 'start' in new_data and 'end' in new_data:
        # ✅ Обработка all-day событий
        if isinstance(new_data['start'], dict) and 'date' in new_data['start']:
            existing['start'] = new_data['start']
            existing['end'] = new_data['end']
        else:
            st = datetime.fromisoformat(new_data['start'])
            en = datetime.fromisoformat(new_data['end'])
            if st.tzinfo is None: st = tz.localize(st)
            if en.tzinfo is None: en = tz.localize(en)
            existing['start'] = {'dateTime': to_iso(st), 'timeZone': TZ_NAME}
            existing['end'] = {'dateTime': to_iso(en), 'timeZone': TZ_NAME}
    
    if 'color' in new_data:
        existing['colorId'] = new_data['color']
    
    try:
        ev = svc.events().update(calendarId='primary', eventId=event_id, body=existing).execute()
        return True, f"✅ Обновлено!\n{ev.get('htmlLink','')}"
    except Exception as ex:
        logger.error(f"Cal update err: {ex}")
        return False, f"❌ Ошибка: {ex}"

async def delete_event(uid, event_id):
    cr = await get_credentials(uid)
    if not cr: return False, "❌ Сначала подключи Google"
    
    svc = build('calendar','v3',credentials=cr)
    try:
        svc.events().delete(calendarId='primary', eventId=event_id).execute()
        return True, "✅ Удалено!"
    except Exception as ex:
        err_str = str(ex).lower()
        if "404" in err_str or "notfound" in err_str:
            return True, "✅ Уже удалено"  # Считаем успешным, если уже нет
        logger.error(f"Cal delete err: {ex}")
        return False, f"❌ Ошибка: {ex}"

async def get_schedule(uid, period="day", target=None, off=0, lim=20):
    cr = await get_credentials(uid)
    if not cr: return False, "❌ Сначала подключи Google", False, None, []
    
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
    else: return False, "❌ Неизвестный период", False, None, []
    
    items = []
    
    # 1. Calendar Events
    try:
        cal = build('calendar','v3',credentials=cr)
        seen_ids = set()
        for e in cal.events().list(calendarId='primary',timeMin=to_iso(st),timeMax=to_iso(en),singleEvents=True,orderBy='startTime').execute().get('items',[]):
            eid = e.get('id')
            if eid in seen_ids: continue
            seen_ids.add(eid)
            
            sdt = parse_dt(e.get('start',{}).get('dateTime') or e.get('start',{}).get('date'))
            items.append({
                'summary':e.get('summary',''),
                'description':e.get('description',''),
                'location':e.get('location',''),
                'start':e.get('start',{}),
                'end':e.get('end',{}),
                '_is_native_task':False,
                '_sort_dt':sdt,
                '_dk':sdt.strftime("%Y-%m-%d") if sdt else None,
                '_eid':eid
            })
    except Exception as ex: logger.error(f"Cal API: {ex}")
    
    # 2. Tasks API
    try:
        ts = build('tasks','v1',credentials=cr)
        tmin = (st - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        tmax = (en + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
        
        for t in ts.tasks().list(tasklist='@default',dueMin=tmin,dueMax=tmax,showCompleted=False,showHidden=False).execute().get('items',[]):
            due = t.get('due')
            if not due: continue
            due_dt = parse_dt(due)
            if not due_dt or due_dt < st or due_dt > en: continue
            
            items.append({
                'summary':t['title'],
                'description':t.get('notes',''),
                'location':'',
                'start':{'dateTime':due},
                'end':{'dateTime':due},
                '_is_native_task':True,
                '_sort_dt':due_dt,
                '_dk':due_dt.strftime("%Y-%m-%d"),
                '_eid':t['id']
            })
    except Exception as ex: logger.error(f"Tasks API: {ex}")

    # Убираем дубли
    deduped = {}
    for item in items:
        key = (item['summary'].strip().lower(), item.get('_dk') or '')
        existing = deduped.get(key)
        if existing:
            curr_has_time = 'dateTime' in item['start']
            exist_has_time = 'dateTime' in existing['start']
            if curr_has_time and not exist_has_time:
                deduped[key] = item
        else:
            deduped[key] = item
    items = list(deduped.values())
    
    # Сортировка и группировка
    items.sort(key=lambda x: x.get('_sort_dt') or datetime.max.replace(tzinfo=tz))
    for it in items: it['_typ'] = detect_type(it)
    
    grp = {}
    for it in items:
        dk = it.get('_dk')
        if not dk: continue
        grp.setdefault(it['_typ'],{}).setdefault(dk,[]).append(it)
        
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
            dgrp.setdefault(it['_typ'],{}).setdefault(dk,[]).append(it)
            
        for typ in TYPE_ORDER:
            if typ in dgrp:
                txt += f"{TYPE_EMOJI[typ]}:\n"
                for dk in sorted(dgrp[typ].keys()):
                    txt += f"🗓 {datetime.strptime(dk,'%Y-%m-%d').strftime('%d.%m.%Y')}\n"
                    for it in dgrp[typ][dk]: txt += fmt_evt(it)+"\n"
                    txt += "\n"
    
    # ✅ Возвращаем 5 значений
    return True, txt.strip(), more, {"start":st,"end":en}, pag