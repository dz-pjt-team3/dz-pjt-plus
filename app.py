from flask import Flask, render_template, request, redirect, url_for, send_file
import os
import re
import requests
import markdown
import tempfile
from openai import OpenAI
from dotenv import load_dotenv
from weasyprint import HTML

# ✅ 환경 변수(.env)에서 API 키 불러오기
load_dotenv()
app = Flask(__name__)

# ✅ OpenAI 클라이언트 초기화
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ✅ GPT에게 여행 일정 요청 → 마크다운 형식으로 응답받기
def generate_itinerary(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "당신은 전문 여행 일정 플래너입니다."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"에러 발생: {e}"

# ✅ 텍스트에서 "..."로 감싼 장소명 추출
def extract_places(text: str) -> list:
    pattern = r"['‘“\"](.+?)['’”\"]"
    matches = re.findall(pattern, text)
    return list(set(matches))

# ✅ 추출한 장소명에 span 태그로 링크 효과 주기
def linkify_places(html: str, place_names: list) -> str:
    for place in place_names:
        html = html.replace(
            place,
            f'<span class="place-link" data-name="{place}">{place}</span>'
        )
    return html

# ✅ 카카오 API로 장소명 → 위도/경도 좌표 변환
def get_kakao_coords(place_name: str):
    KEY = os.environ["KAKAO_REST_API_KEY"]
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KEY}"}
    params = {"query": place_name}
    res = requests.get(url, headers=headers, params=params).json()
    if res.get('documents'):
        lat = res['documents'][0]['y']
        lng = res['documents'][0]['x']
        return lat, lng
    return None

# ✅ GPT로부터 받은 텍스트를 일정 리스트로 파싱
def extract_schedule_entries(text: str) -> list:
    pattern = r"(\d+일차)(?:\s*[:\-]?\s*)?(.*?)(?=\d+일차|$)"
    entries = re.findall(pattern, text, re.DOTALL)
    schedule = []
    for day, body in entries:
        for line in body.strip().split("\n"):
            time_match = re.match(r"(\d{1,2}:\d{2})", line)
            time = time_match.group(1) if time_match else ""
            place_match = re.search(r"[\"“‘'](.+?)[\"”’']", line)
            if place_match:
                place = place_match.group(1)
                desc = line.replace(place_match.group(0), "").strip(" :-~")
                schedule.append({
                    "day": day,
                    "time": time,
                    "place": place,
                    "desc": desc
                })
    return schedule

# ✅ 카테고리 코드로 장소 검색 (맛집, 카페 등)
def search_category(category_code: str, region: str, size=15) -> list:
    REST_KEY = os.environ["KAKAO_REST_API_KEY"]
    url = "https://dapi.kakao.com/v2/local/search/category.json"
    headers = {"Authorization": f"KakaoAK {REST_KEY}"}
    params = {
        "category_group_code": category_code,
        "query": region,
        "size": size
    }
    res = requests.get(url, headers=headers, params=params).json()
    return res.get("documents", [])

# ✅ 메인페이지: 히어로 + 추천 링크
@app.route("/")
def index():
    return render_template("index.html")

# ✅ 음식점 추천 페이지
@app.route("/food", methods=["GET", "POST"])
def food():
    places = []
    youtube_videos = []
    if request.method == "POST":
        region = request.form.get("region")
        REST_KEY = os.environ["KAKAO_REST_API_KEY"]
        url = "https://dapi.kakao.com/v2/local/search/keyword.json"
        headers = {"Authorization": f"KakaoAK {REST_KEY}"}
        params = {"query": f"{region} 맛집", "size": 10}
        try:
            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            data = res.json()
            places = [
                {
                    "name": doc["place_name"],
                    "address": doc["road_address_name"],
                    "lat": doc["y"],
                    "lng": doc["x"]
                } for doc in data["documents"]
            ]
        except Exception as e:
            places = [{"name": f"에러 발생: {e}", "address": ""}]
        youtube_videos = search_youtube_videos(f"{region} 맛집")
    return render_template("food.html", places=places, youtube_videos=youtube_videos, kakao_key=os.environ["KAKAO_JAVASCRIPT_KEY"])

@app.route("/cafe")
def cafe():
    return render_template("cafe.html")

@app.route("/acc")
def acc():
    return render_template("acc.html")

# ✅ 일정 생성 및 지도 표시
@app.route("/plan", methods=["GET", "POST"])
def plan():
    result = ""
    markers = []
    center_lat, center_lng = 36.5, 127.5  # 기본 지도 중심 좌표 (대한민국 중심쯤)

    # ✅ 사용자 입력값을 저장하는 form 딕셔너리 (초기화)
    form = {
        "start_date": "",
        "end_date": "",
        "companions": "",
        "people_count": "",
        "location": "",
        "transport_mode": "",
        "theme": [],
        "user_prompt": ""
    }

    if request.method == "POST":
        # ✅ 입력값 form에 저장
        for key in form:
            form[key] = request.form.getlist(key) if key == "theme" else request.form.get(key)

        # ✅ 지도 중심 좌표 업데이트
        coords = get_kakao_coords(form["location"])
        if coords:
            center_lat, center_lng = coords

        # ✅ GPT 프롬프트 생성
        prompt = f"""
        여행 날짜: {form['start_date']} ~ {form['end_date']}
        동행: {form['companions']}, 총 인원: {form['people_count']}명
        여행지: {form['location']}, 테마: {', '.join(form['theme'])}
        교통수단: {form['transport_mode']}
        추가 조건: {form['user_prompt']}

        **출력 형식**
        1일차:
        09:00~10:00: \"해운대 해수욕장\"
        - 해운대의 상징인 해변에서 아침을 맞이합니다.
        - 각 일정에 대해 성의있는 설명과 장소 추천
        - 모든 장소는 반드시 {form['location']} 지역 내
        - 장소명은 큰따옴표(\"...\")로 묶기
        """

        # ✅ GPT 호출 → HTML 변환 + 링크 처리
        raw_result = generate_itinerary(prompt)
        result = markdown.markdown(raw_result)
        result = linkify_places(result, extract_places(raw_result))

        # ✅ 지도용 마커 데이터 추출
        for entry in extract_schedule_entries(raw_result):
            coord = get_kakao_coords(entry["place"])
            if coord:
                markers.append({"name": entry["place"], "lat": coord[0], "lng": coord[1], "day": entry["day"], "time": entry["time"], "desc": entry["desc"]})

    return render_template("plan.html", result=result, kakao_key=os.environ["KAKAO_JAVASCRIPT_KEY"], markers=markers, center_lat=center_lat, center_lng=center_lng, form=form)

# ✅ 카테고리별 장소 추천
@app.route("/search/<category>")
def search(category):
    code_map = {"cafe": "CE7", "restaurant": "FD6", "tourism": "AT4"}
    code = code_map.get(category)
    if not code:
        return redirect(url_for("index"))
    region = request.args.get("region", "")
    places = search_category(code, region)
    return render_template("search.html", category=category, region=region, places=places)

# ✅ 유튜브 맛집 영상 검색 함수
def search_youtube_videos(query, max_results=5):
    api_key = os.environ["YOUTUBE_API_KEY"]
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {"part": "snippet", "q": query, "type": "video", "maxResults": max_results, "key": api_key}
    res = requests.get(url, params=params)
    videos = []
    if res.status_code == 200:
        data = res.json()
        for item in data["items"]:
            video_id = item["id"]["videoId"]
            title = item["snippet"]["title"]
            thumbnail = item["snippet"]["thumbnails"]["medium"]["url"]
            videos.append({"title": title, "url": f"https://www.youtube.com/watch?v={video_id}", "thumbnail": thumbnail})
    return videos

# ✅ PDF 다운로드 라우터 (HTML 문자열 → PDF 변환)
@app.route("/download_pdf", methods=["POST"])
def download_pdf():
    raw_html = request.form["result_html"]
    rendered_html = render_template("pdf_template.html", result=raw_html)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmpfile:
        HTML(string=rendered_html).write_pdf(tmpfile.name)
        return send_file(tmpfile.name, as_attachment=True, download_name="여행일정.pdf")

# ✅ 앱 실행 시작점
if __name__ == "__main__":
    app.run(debug=True)