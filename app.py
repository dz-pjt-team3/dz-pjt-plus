from flask import Flask, render_template, request, redirect, url_for, make_response
import json
import os
import re
import requests
import markdown
from openai import OpenAI
from dotenv import load_dotenv
from weasyprint import HTML
from datetime import datetime, timedelta
import uuid

# 환경변수(.env)에서 API 키 로드
load_dotenv()
app = Flask(__name__)

# OpenAI 클라이언트 생성
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
SHARE_FILE = 'share_data.json'    # ✅ 공유된 일정 저장 파일
REVIEW_FILE = 'review_data.json'  # ✅ 리뷰 저장 파일

# ✅ 리뷰 불러오기
def load_reviews():
    if not os.path.exists(REVIEW_FILE):
        return []
    with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

# ✅ 리뷰 저장
def save_review(new_review):
    reviews = load_reviews()
    reviews.append(new_review)
    with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
        json.dump(reviews, f, ensure_ascii=False, indent=2)

# ✅ 공유 일정 저장/불러오기

def save_shared_plan(plan_html):
    share_id = str(uuid.uuid4())[:8]  # 짧은 UUID 생성
    if os.path.exists(SHARE_FILE):
        with open(SHARE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {}
    data[share_id] = plan_html
    with open(SHARE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return share_id

def load_shared_plan(share_id):
    if not os.path.exists(SHARE_FILE):
        return None
    with open(SHARE_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get(share_id)

@app.route("/share/<share_id>")
def shared_plan(share_id):
    html = load_shared_plan(share_id)
    if html is None:
        return "공유된 일정을 찾을 수 없습니다.", 404
    return render_template("shared_plan.html", result=html)

@app.route("/save_share", methods=["POST"])
def save_share():
    html = request.json.get("html", "")
    share_id = save_shared_plan(html)
    return jsonify({"share_id": share_id})

# ✅ 일정 텍스트 생성 (GPT)
def generate_itinerary(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "당신은 전문 여행 일정 플래너입니다."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"에러 발생: {e}"

# ✅ 기타 도우미 함수들
def extract_places(text: str) -> list:
    pattern = r"['‘“\"](.+?)['’”\"]"
    return list(set(re.findall(pattern, text)))

def linkify_places(html: str, place_names: list) -> str:
    for place in place_names:
        html = html.replace(
            place,
            f'<span class="place-link" data-name="{place}">{place}</span>'
        )
    return html

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

def extract_schedule_entries(text: str) -> list:
    pattern = r"(\d+일차)(?:\s*[:\-]?\s*)?(.*?)(?=\n\d+일차|$)"
    entries = re.findall(pattern, text, re.DOTALL)
    schedule = []
    for day, body in entries:
        for line in body.strip().split("\n"):
            time_match = re.match(r"(\d{1,2}:\d{2})", line)
            time = time_match.group(1) if time_match else ""
            place_match = re.search(r'["“‘\'](.+?)["”’\']', line)
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

# ✅ 인덱스 페이지
@app.route('/')
def index():
    return render_template('index.html')

# ✅ 리뷰 제출 처리
@app.route('/submit_review', methods=['POST'])
def submit_review():
    rating = request.form.get('rating')
    comment = request.form.get('comment')
    if rating and comment:
        save_review({"rating": rating, "comment": comment})
    return redirect(url_for('plan'))

# ✅ PDF 다운로드
@app.route("/download_pdf", methods=["POST"])
def download_pdf():
    result_html = request.form.get("result_html")
    rendered = render_template("pdf_template.html", result=result_html)
    pdf = HTML(string=rendered).write_pdf()
    response = make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = "attachment; filename=travel_plan.pdf"
    return response

# ✅ 메인 기능 페이지
@app.route("/plan", methods=["GET", "POST"])
def plan():
    result = ""
    markers = []
    center_lat, center_lng = 36.5, 127.5

    # 폼 입력값 초기화
    start_date = end_date = companions = people_count = user_prompt = location = transport_mode = ""
    theme = []

    if request.method == "POST":
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        companions = request.form.get("companions")
        people_count = request.form.get("people_count")
        theme = request.form.getlist("theme")
        theme_str = ", ".join(theme)
        user_prompt = request.form.get("user_prompt")
        location = request.form.get("location")
        transport_mode = request.form.get("transport_mode")

        coords = get_kakao_coords(location)
        if coords:
            center_lat, center_lng = coords

        # GPT 프롬프트 구성
        prompt = f"""
        여행 날짜: {start_date} ~ {end_date}
        동행: {companions}, 총 인원: {people_count}명
        여행지: {location}, 테마: {theme_str}
        교통수단: {transport_mode}
        추가 조건: {user_prompt}
        
        - 각 일차를 꼭 출력할 것
        - 일정은 시각부터 시작 (09:00 등)
        - 장소명은 큰따옴표로 묶기
        - 반드시 {location} 지역 내의 장소만 포함
        """

        raw_result = generate_itinerary(prompt)

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            days = (end_dt - start_dt).days + 1
            for i in range(days):
                tag = f"{i+1}일차"
                full_label = f"{tag}: {(start_dt + timedelta(days=i)).strftime('%Y-%m-%d (%A)')}"
                if tag in raw_result:
                    raw_result = raw_result.replace(tag, full_label)
        except Exception as e:
            print("📛 요일 계산 오류:", e)

        result = markdown.markdown(raw_result)
        place_names = extract_places(raw_result)
        result = linkify_places(result, place_names)

        schedule_data = extract_schedule_entries(raw_result)
        for entry in schedule_data:
            coord = get_kakao_coords(entry["place"])
            if coord:
                markers.append({
                    "name": entry["place"],
                    "lat": coord[0],
                    "lng": coord[1],
                    "day": entry["day"],
                    "time": entry["time"],
                    "desc": entry["desc"]
                })

    reviews = load_reviews()
    return render_template("plan.html",
                           result=result,
                           kakao_key=os.environ["KAKAO_JAVASCRIPT_KEY"],
                           markers=markers,
                           center_lat=center_lat,
                           center_lng=center_lng,
                           start_date=start_date,
                           end_date=end_date,
                           companions=companions,
                           people_count=people_count,
                           theme=theme,
                           user_prompt=user_prompt,
                           location=location,
                           transport_mode=transport_mode,
                           reviews=reviews)

# ✅ 추천 카테고리 라우트
@app.route("/search/<category>")
def search(category):
    code_map = {
        "cafe": "CE7",
        "restaurant": "FD6",
        "tourism": "AT4",
    }
    code = code_map.get(category)
    if not code:
        return redirect(url_for("index"))
    region = request.args.get("region", "")
    places = search_category(code, region)
    return render_template("search.html", category=category, region=region, places=places)

def search_category(category_code: str, region: str, size=15) -> list:
    REST_KEY = os.environ["KAKAO_REST_API_KEY"]
    url = "https://dapi.kakao.com/v2/local/search/category.json"
    headers = {"Authorization": f"KakaoAK {REST_KEY}"}
    params = {"category_group_code": category_code, "query": region, "size": size}
    res = requests.get(url, headers=headers, params=params).json()
    return res.get("documents", [])

@app.route("/food")
def food():
    return redirect(url_for("search", category="restaurant"))

@app.route("/cafe")
def cafe():
    return redirect(url_for("search", category="cafe"))

@app.route("/acc")
def acc():
    return redirect(url_for("search", category="tourism"))

if __name__ == '__main__':
    app.run(debug=True)