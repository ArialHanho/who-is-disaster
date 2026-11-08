"""Small optional-connections popover, separate from repository settings."""
import time
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QVBoxLayout,QHBoxLayout,QCheckBox,QComboBox,QLineEdit
from .review_theme import RoundedDialog,STYLE,label,button,scroll_body,size_popup,BROWN,MUTED
from .companion_weather import search_cities


class OtherConnectionsDialog(RoundedDialog):
    def __init__(self,panel):
        super().__init__(panel)
        self.panel,self.chat=panel,panel.idle_chat
        self.setWindowTitle('코드 비서 · 기타 연결')
        self.setWindowFlags(Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        size_popup(self,width=440,height=640)
        self.move(panel.pos())
        outer=QVBoxLayout(self)
        outer.setContentsMargins(18,14,18,16)
        heading=QHBoxLayout()
        heading.addWidget(label('기타 연결',17,BROWN,True),1)
        heading.addWidget(button('✕',self.accept,role='icon'))
        outer.addLayout(heading)
        _,body=scroll_body(outer)
        body.setContentsMargins(0,8,0,8)
        body.addWidget(label('가끔, 가볍게 말 걸어요',16,BROWN,True))
        self.enabled=QCheckBox('대기 중 한마디')
        self.enabled.setChecked(bool(self.chat.store.data['enabled']))
        body.addWidget(self.enabled)
        body.addWidget(label('“오늘은 어때요~”처럼 짧게 인사해요.\n분석 중이거나 다른 알림을 읽을 때는 기다려요.',13,MUTED))
        self.interval=QComboBox()
        self.interval.setAccessibleName('한마디 간격')
        for minutes in (10,20,30,60): self.interval.addItem(f'{minutes}분에 한 번 정도',minutes)
        self.interval.setCurrentIndex(self.interval.findData(self.chat.store.data['interval_minutes']))
        body.addWidget(self.interval)
        self.focus=QCheckBox('전체화면에서는 한마디 쉬기')
        self.focus.setChecked(bool(self.chat.store.data['pause_fullscreen']))
        self.focus.toggled.connect(lambda value:self.configure(pause_fullscreen=value))
        body.addWidget(self.focus)
        row=QHBoxLayout()
        self.preview=button('한마디 듣기',self.hear,True)
        self.preview.setEnabled(not panel.busy)
        row.addWidget(self.preview)
        row.addWidget(button('오늘은 조용히',self.chat.quiet_today,role='chip'))
        body.addLayout(row)
        self.quiet=label('',12,MUTED)
        body.addWidget(self.quiet)
        self.resume=button('다시 말 걸기',self.resume_chat,role='chip')
        body.addWidget(self.resume)
        body.addWidget(label('현재 위치 날씨',16,BROWN,True))
        body.addWidget(label('현재 위치 주변의 날씨도 가끔 알려드려요. 위치는 날씨 조회에만 사용해요.',13,MUTED))
        row=QHBoxLayout()
        self.locate_button=button('현재 위치 사용',self.chat.locate)
        row.addWidget(self.locate_button)
        row.addWidget(button('위치 설정',lambda:QDesktopServices.openUrl(QUrl('ms-settings:privacy-location')),role='chip'))
        body.addLayout(row)
        self.weather=label('',13,MUTED)
        body.addWidget(self.weather)
        self.weather_toggle=QCheckBox('한마디에 날씨도 포함')
        self.weather_toggle.setChecked(bool(self.chat.store.data['weather_enabled']))
        body.addWidget(self.weather_toggle)
        row=QHBoxLayout()
        self.city=QLineEdit()
        self.city.setPlaceholderText('또는 지역 이름 · 예: 대전')
        self.city.setMaxLength(80)
        self.city.setAccessibleName('날씨 지역 이름')
        row.addWidget(self.city,1)
        self.search=button('검색',self.find_city,role='chip')
        row.addWidget(self.search)
        body.addLayout(row)
        self.results=QComboBox()
        self.results.setAccessibleName('검색된 날씨 지역')
        self.results.hide()
        body.addWidget(self.results)
        self.select=button('이 지역으로 연결',self.select_city,role='chip')
        self.select.hide()
        body.addWidget(self.select)
        self.disconnect=button('날씨 연결 해제',self.chat.disconnect_weather,role='icon')
        body.addWidget(self.disconnect)
        body.addWidget(label('날씨 제공: Open-Meteo · 한마디에는 AI 사용량이 들지 않아요.',11,MUTED))
        outer.addWidget(button('닫기',self.accept))
        self.enabled.toggled.connect(lambda value:self.configure(enabled=value))
        self.interval.currentIndexChanged.connect(lambda:self.configure(interval_minutes=self.interval.currentData()))
        self.weather_toggle.toggled.connect(self.toggle_weather)
        self.chat.changed.connect(self.refresh)
        self.refresh()

    def configure(self,**values):
        self.chat.store.configure(**values)
        self.refresh()

    def refresh(self):
        data=self.chat.store.data
        quiet=data['muted_until']>time.time()
        quiet_text='잠시 한마디를 쉬어요.' if data.get('quiet_kind')=='hour' else '오늘은 한마디를 쉬어요.'
        self.quiet.setText(quiet_text+' 코드 알림은 계속 알려드려요.' if quiet else '설정은 바로 저장돼요.')
        self.resume.setVisible(quiet)
        self.weather.setText(self.chat.weather_status)
        self.weather_toggle.blockSignals(True)
        self.weather_toggle.setChecked(bool(data['weather_enabled']))
        self.weather_toggle.blockSignals(False)
        self.weather_toggle.setEnabled(bool(data['location']))
        self.disconnect.setVisible(bool(data['location']))
        self.locate_button.setEnabled(self.chat.source is None)

    def resume_chat(self):
        self.configure(muted_until=0,enabled=True)
        self.enabled.setChecked(True)

    def hear(self):
        self.accept()
        self.panel.hide()
        if self.panel.sprite: self.panel.sprite.bubble.hide()
        QTimer.singleShot(0,lambda:self.chat.speak(manual=True))

    def toggle_weather(self,value):
        if not value:
            self.chat.generation+=1
            self.chat.stop_location()
            self.chat.weather=None
        self.configure(weather_enabled=value)
        if value:self.chat.refresh_weather()

    def find_city(self):
        query=self.city.text().strip()
        self.search.setEnabled(False)
        self.results.hide();self.select.hide()
        self.weather.setText('지역을 찾고 있어요…')
        self.chat.run(lambda:search_cities(query),self.cities_received,self.city_error)

    def cities_received(self,items):
        self.search.setEnabled(True)
        self.results.clear()
        for item in items:self.results.addItem(item['label'],item)
        self.results.setVisible(bool(items));self.select.setVisible(bool(items))
        self.weather.setText('날씨를 볼 지역을 골라 주세요.' if items else '지역을 찾지 못했어요. 도시 이름으로 다시 검색해 주세요.')

    def city_error(self,_):
        self.search.setEnabled(True)
        self.weather.setText('지역을 찾지 못했어요. 이름과 연결 상태를 확인해 주세요.')

    def select_city(self):
        self.chat.stop_location()
        self.chat.connect_location(self.results.currentData())
        self.results.hide();self.select.hide()

    def done(self,result):
        try:self.chat.changed.disconnect(self.refresh)
        except RuntimeError:pass
        super().done(result)
