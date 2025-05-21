<<<<<<< HEAD
<<<<<<< HEAD
<DZ_MINI_PROJECT_TEAM3>
# 구성
travelapp/
├── app.py
├── templates/
├── .env
│   ├── base.html
│   └── index.html
├── static/
│   └── style.css
└── itinerary_generator.py

# install
pip install Flask            # 웹 프레임워크
pip install python-dotenv    # .env 환경변수 로드
pip install openai           # OpenAI API 클라이언트
pip install requests         # Kakao REST API 호출용
pip install markdown         # GPT 응답(마크다운→HTML) 변환

---------------------↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓---------------------
# 배포
requirements.txt
L Flask
L python-dotenv
L openai
L requests
L markdown

app.py파일에 pip install -r requirements.txt 삽입

