"""Optional light conversation; repository notices always take priority."""
import json
import random
import time
from datetime import datetime, timedelta, timezone
from PySide6.QtCore import QObject, QTimer, QThreadPool, Signal
from PySide6.QtWidgets import QApplication
from .background import Worker
from .companion_weather import fetch_weather, valid_location, weather_text
from .desktop_focus import fullscreen_active

LINES=(
    '오늘은 어때요~ 저는 여기서 같이 있고 있어요.',
    '여긴 조용하네요. 잠깐 쉬어 가는 중인가요?',
    '오늘은 코드도 잠깐 쉬는 날인가요? 그런 날도 좋죠.',
    '잠깐 인사하러 왔어요. 너무 바쁘면 조용히 기다릴게요.',
    '물 한 모금 마셨어요? 저도 잠깐 쉬어 가자고 말 걸어 봤어요.',
    '아무 말 없이 같이 있어도 괜찮아요. 필요하면 톡 눌러 주세요.',
    '기지개 한 번 쭉 펴 볼까요? 저는 여기 있을게요.',
    '오늘은 어떤 기분이에요? 가끔은 천천히 가도 좋아요.',
    '잠깐 눈을 쉬어 가도 좋아요. 먼 곳도 한 번 봐요.',
    '커피가 생각나는 순간이네요. 잠깐 쉬었다 갈까요?',
    '쉬는 시간에는 무슨 생각을 해요? 저는 그냥 인사하러 왔어요.',
    '오늘도 반가워요. 별일 없어도 가끔 말 걸게요.',
    '오늘의 작은 목표는 뭐예요? 하나만 정해도 좋죠.',
    '잠깐 멍 때리는 시간도 꽤 괜찮아요~',
    '오늘 배경 음악은 어떤 분위기예요?',
    '간식 하나 고른다면 달콤한 쪽인가요, 짭짤한 쪽인가요?',
    '창밖도 잠깐 구경해요. 화면은 여기 있을 거예요.',
    '변수 이름 하나에 오래 고민하게 될 때 있죠?',
    '“잠깐만 고치자”가 긴 여행이 될 때도 있죠~',
    '괄호 짝 찾기는 가끔 숨바꼭질 같아요.',
    '잘 지은 이름 하나가 긴 설명보다 편할 때도 있죠.',
    '오늘 새로 배운 게 하나 있다면 뭐예요?',
    '떠오른 아이디어는 한 줄만 적어 둬도 좋겠어요.',
    '생각이 안 풀릴 때는 잠깐 다른 걸 봐도 괜찮아요.',
    '복잡한 것도 작은 조각부터 보면 덜 막막하죠.',
    '지금은 커피 한 잔과 차 한 잔 중 어느 쪽이에요?',
    '키보드 소리에도 취향이 있더라고요. 어떤 소리가 좋아요?',
    '오늘 작업이 끝나면 하고 싶은 일이 있나요?',
    '좋아하는 노래 한 곡만큼 쉬어 가는 것도 좋겠어요.',
    '바로 답이 안 나와도 괜찮아요. 잠깐 두고 봐요.',
    '작은 할 일 하나 끝내는 기분도 꽤 좋죠~',
    '메모해 둔 아이디어를 나중에 다시 보면 재밌어요.',
    '여백이 있는 화면처럼, 하루에도 여백이 있으면 좋겠어요.',
    '오늘은 어떤 부분이 가장 재밌었어요?',
    '잠깐 산책하는 상상만 해도 기분이 달라지네요.',
    '저는 여기서 작은 응원 하나 보낼게요~',
)
DEFAULTS={'enabled':False,'interval_minutes':20,'muted_until':0,'last_spoken':0,
          'recent':[],'weather_enabled':False,'location':None,'last_weather_spoken':0,
          'pause_fullscreen':True,'quiet_kind':'today'}


class ChatStore:
    def __init__(self, root, clock=time.time, rng=None):
        self.path=root/'.data/character/idle-chat.json'
        self.clock,self.rng=clock,rng or random.Random()
        try:
            raw=json.loads(self.path.read_text(encoding='utf-8'))
            self.data={**DEFAULTS,**raw} if isinstance(raw,dict) else dict(DEFAULTS)
        except (OSError,ValueError): self.data=dict(DEFAULTS)
        if self.data['interval_minutes'] not in (10,20,30,60): self.data['interval_minutes']=20
        for key in ('muted_until','last_spoken','last_weather_spoken'):
            if type(self.data[key]) not in (int,float): self.data[key]=0
        self.data['recent']=[line for line in self.data['recent'] if line in LINES][-6:] if isinstance(self.data['recent'],list) else []
        if not valid_location(self.data['location']): self.data['location']=None
        self.ready_at=max(clock()+120,self.data['last_spoken']+self.data['interval_minutes']*60)

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        pending=self.path.with_suffix('.tmp')
        pending.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        pending.replace(self.path)

    def configure(self, **values):
        self.data.update(values)
        self.ready_at=max(self.clock()+120,self.data['last_spoken']+self.data['interval_minutes']*60)
        self.save()

    def due(self):
        return self.data['enabled'] and self.clock()>=max(self.ready_at,self.data['muted_until'])

    def choose(self):
        return self.rng.choice([line for line in LINES if line not in self.data['recent']])

    def spoken(self, text, weather=False):
        if text in LINES: self.data['recent']=(self.data['recent']+[text])[-6:]
        now=self.clock()
        self.data['last_spoken']=now
        if weather: self.data['last_weather_spoken']=now
        self.ready_at=now+self.data['interval_minutes']*60
        self.save()

    def quiet_today(self):
        local_zone=timezone(timedelta(hours=9))
        tomorrow=datetime.fromtimestamp(self.clock(),local_zone).date()+timedelta(days=1)
        self.data['muted_until']=datetime.combine(tomorrow,datetime.min.time(),local_zone).timestamp()
        self.data['quiet_kind']='today'
        self.save()

    def quiet_hour(self):
        self.data['muted_until']=self.clock()+3600
        self.data['quiet_kind']='hour'
        self.save()


class IdleChat(QObject):
    changed=Signal()

    def __init__(self,panel):
        super().__init__(panel)
        self.panel,self.store=panel,ChatStore(panel.root)
        self.weather=None
        self.weather_status='현재 위치나 지역을 연결해 주세요.'
        self.source=None
        self.workers=[]
        self.generation=0
        self.next_weather=0
        self.focus_blocked=False
        self.focus_resume_at=0
        self.focus_timer=QTimer(self)
        self.focus_timer.timeout.connect(self.check_focus)
        self.focus_timer.start(1000)
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(15000)
        self.location_timeout=QTimer(self)
        self.location_timeout.setSingleShot(True)
        self.location_timeout.timeout.connect(lambda:self.location_failed('위치를 확인하지 못했어요. 지역 이름으로 연결할 수도 있어요.'))
        QTimer.singleShot(2000,self.warm_weather)

    def warm_weather(self):
        location=self.store.data['location']
        if self.store.data['enabled'] and self.store.data['weather_enabled'] and location:
            self.refresh_weather(locate=time.time()-location.get('updated',0)>1800)

    def available(self, manual=False):
        panel,sprite=self.panel,self.panel.sprite
        if (not sprite or not sprite.isVisible() or panel.isVisible() or QApplication.activeModalWidget() or
                panel.busy or panel.queue or panel.changed_repos or sprite.actions.activities or sprite.pressed or sprite.pose_demo):
            return False
        if sprite.bubble.isVisible() and sprite.bubble.mode not in {'chatter','greeting','interaction'}: return False
        if not manual and (sprite.actions.state not in {'idle','sleep'} or sprite.bubble.isVisible()): return False
        if not manual:
            self.check_focus()
            if self.focus_blocked or time.monotonic()<self.focus_resume_at:return False
            for repo in panel.service.repos():
                notice=panel.service.pending_notice(repo['id'])
                if notice['findings'] or notice['error']: return False
        return True

    def check_focus(self):
        enabled=self.store.data['pause_fullscreen']
        blocked=bool(enabled and fullscreen_active())
        if not enabled:self.focus_resume_at=0
        elif self.focus_blocked and not blocked:self.focus_resume_at=time.monotonic()+45
        self.focus_blocked=blocked
        sprite=self.panel.sprite
        if blocked and sprite and sprite.bubble.isVisible() and sprite.bubble.mode=='chatter':
            sprite.bubble.hide()

    def tick(self):
        if not self.store.due() or not self.available(): return
        data=self.store.data
        if data['weather_enabled'] and data['location'] and time.time()>=self.next_weather:
            self.refresh_weather()
        self.speak()

    def speak(self, manual=False):
        if not self.available(manual): return False
        now=time.time()
        forecast=weather_text(self.weather,now) if self.store.data['weather_enabled'] else None
        use_weather=bool(forecast and now-self.store.data['last_weather_spoken']>=3600)
        text,credit=forecast if use_weather else (self.store.choose(),'가벼운 한마디')
        sprite=self.panel.sprite
        sprite.bubble.show_message(text,mode='chatter',auto_hide_ms=10000,credit=credit)
        sprite.place_popup(sprite.bubble)
        sprite.bubble.show()
        self.store.spoken(text,use_weather)
        return True

    def quiet_today(self):
        self.store.quiet_today()
        self.after_quiet()

    def quiet_hour(self):
        self.store.quiet_hour()
        self.after_quiet()

    def after_quiet(self):
        if self.panel.sprite and self.panel.sprite.bubble.mode in {'chatter','greeting','interaction'}:
            self.panel.sprite.bubble.hide()
        self.changed.emit()

    def run(self, fn, result, error):
        worker=Worker(fn)
        self.workers.append(worker)
        worker.signals.result.connect(result)
        worker.signals.error.connect(error)
        worker.signals.finished.connect(lambda:self.workers.remove(worker) if worker in self.workers else None)
        QThreadPool.globalInstance().start(worker)

    def stop_location(self):
        self.location_timeout.stop()
        if self.source:
            source,self.source=self.source,None
            source.stopUpdates()
            source.deleteLater()

    def locate(self):
        from PySide6.QtPositioning import QGeoPositionInfoSource
        self.stop_location()
        self.generation+=1
        self.weather=None
        self.weather_status='Windows에서 현재 위치를 확인하고 있어요…'
        self.changed.emit()
        self.source=QGeoPositionInfoSource.createDefaultSource(self)
        if self.source is None:
            self.location_failed('이 PC에서 위치를 읽을 수 없어요. 지역 이름으로 연결해 주세요.')
            return
        source=self.source
        source.positionUpdated.connect(lambda info:self.located(info) if self.source is source else None)
        source.errorOccurred.connect(lambda error:self.location_error(error) if self.source is source else None)
        self.changed.emit()
        self.location_timeout.start(17000)
        self.source.requestUpdate(15000)

    def location_error(self,error):
        from PySide6.QtPositioning import QGeoPositionInfoSource as Source
        if error==Source.Error.NoError: return
        text=('Windows 위치 권한이 꺼져 있어요. 위치 설정에서 허용하거나 지역 이름으로 연결해 주세요.'
              if error in {Source.Error.AccessError,Source.Error.ClosedError} else
              '현재 위치를 확인하지 못했어요. 잠시 뒤 다시 시도하거나 지역을 골라 주세요.')
        self.location_failed(text)

    def location_failed(self,text):
        self.stop_location()
        self.weather_status=text
        self.changed.emit()
        # No stale saved location is silently labelled as the current location.
        self.next_weather=time.time()+1800

    def located(self,info):
        if not self.source: return
        coordinate=info.coordinate()
        if not coordinate.isValid():
            self.location_failed('현재 위치가 정확하지 않아요. 지역 이름으로 연결해 주세요.')
            return
        location={'latitude':round(coordinate.latitude(),2),'longitude':round(coordinate.longitude(),2),
                  'label':'현재 위치 주변','mode':'device','updated':time.time()}
        self.stop_location()
        self.connect_location(location)

    def connect_location(self,location):
        if not valid_location(location): return
        self.generation+=1
        self.weather=None
        self.store.configure(location=location,weather_enabled=True)
        self.refresh_weather(locate=False)

    def refresh_weather(self,locate=True):
        data=self.store.data
        if not data['weather_enabled'] or not data['location']: return
        self.next_weather=time.time()+1800
        if locate and data['location'].get('mode')=='device':
            self.weather=None
            self.locate()
            return
        generation=self.generation
        self.weather_status='날씨를 확인하고 있어요…'
        self.changed.emit()
        def received(result):
            if generation!=self.generation: return
            self.weather=result
            formatted=weather_text(result,time.time())
            self.weather_status=(formatted[0]+'\n'+formatted[1]) if formatted else '최신 날씨를 확인하지 못했어요.'
            self.changed.emit()
        def failed(_):
            if generation!=self.generation: return
            self.weather=None
            self.weather_status='날씨에 연결하지 못했어요. 한마디는 계속 사용할 수 있어요.'
            self.changed.emit()
        location=dict(data['location'])
        self.run(lambda:fetch_weather(location),received,failed)

    def disconnect_weather(self):
        self.generation+=1
        self.stop_location()
        self.weather=None
        self.store.configure(weather_enabled=False,location=None)
        self.weather_status='날씨 연결을 해제했어요.'
        self.changed.emit()

    def close(self):
        self.timer.stop()
        self.focus_timer.stop()
        self.stop_location()
        self.generation+=1
