"""Current weather and city lookup. Network requests never run on the UI thread."""
import json
import math
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def read_json(endpoint, params):
    request = Request(endpoint+'?'+urlencode(params), headers={'User-Agent':'CodeCompanion/0.1'})
    with urlopen(request, timeout=8) as response:
        raw = response.read(500_001)
    if len(raw)>500_000: raise ValueError('날씨 응답이 너무 커요.')
    return json.loads(raw)


def valid_location(value):
    return (isinstance(value, dict) and
            all(type(value.get(k)) in (float,int) and math.isfinite(value[k]) and abs(value[k])<=limit
                for k,limit in [('latitude',90),('longitude',180)]))


def search_cities(name):
    if not 2<=len(name.strip())<=80: raise ValueError('지역 이름을 두 글자 이상 입력해 주세요.')
    data=read_json('https://geocoding-api.open-meteo.com/v1/search',
                   {'name':name.strip(),'count':5,'language':'ko','format':'json'})
    return [{'label':' · '.join(filter(None,[item.get('name'),item.get('admin1'),item.get('country')])),
             'latitude':round(item['latitude'],2),'longitude':round(item['longitude'],2),'mode':'city'}
            for item in data.get('results',[]) if valid_location(item)]


def fetch_weather(location):
    if not valid_location(location): raise ValueError('날씨를 볼 위치를 먼저 연결해 주세요.')
    data=read_json('https://api.open-meteo.com/v1/forecast',{
        'latitude':location['latitude'],'longitude':location['longitude'],
        'current':'temperature_2m,apparent_temperature,weather_code',
        'timezone':'auto','forecast_days':1})
    current=data.get('current',{})
    temp,code=current.get('temperature_2m'),current.get('weather_code')
    if (type(temp) not in (int,float) or not math.isfinite(temp) or not -90<=temp<=65 or type(code) is not int):
        raise ValueError('지금 날씨 값을 확인하지 못했어요.')
    stamp=datetime.fromisoformat(current['time']).replace(tzinfo=timezone.utc).timestamp()-data.get('utc_offset_seconds',0)
    return {'temperature':temp,'code':code,'stamp':stamp,'label':location.get('label','현재 위치 주변')}


def weather_text(weather, now):
    """Old data never masquerades as current weather."""
    if not weather or not -300<=now-weather['stamp']<=3600: return None
    code=weather['code']
    if code==0: condition='맑음'
    elif code in (1,2): condition='구름 조금'
    elif code==3: condition='흐림'
    elif code in (45,48): condition='안개'
    elif code in (51,53,55,56,57): condition='이슬비'
    elif code in (61,63,65,66,67,80,81,82): condition='비'
    elif code in (71,73,75,77,85,86): condition='눈'
    elif code in (95,96,99): condition='뇌우'
    else: condition='날씨 정보'
    temp=weather['temperature']
    follow=('비가 와요. 우산 챙겨요.' if condition in {'비','이슬비','뇌우'} else
            '눈이 와요. 나갈 땐 조심해요.' if condition=='눈' else
            '나갈 땐 따뜻하게 입어요.' if temp<10 else
            '덥네요. 물 한 잔 챙겨요.' if temp>=28 else '잠깐 바깥바람 쐴까요?')
    place='지금' if weather['label']=='현재 위치 주변' else weather['label'].split(' · ')[0]+'은'
    text=f"{place} {temp:.0f}°C예요.\n{follow}"
    credit='Open-Meteo · '+datetime.fromtimestamp(weather['stamp']).strftime('%H:%M')+' 기준'
    return text,credit
