# Archelp

## Database
cd ~/archelp
docker compose up -d
docker compose ps

## Django
cd ~/archelp
source .venv/bin/activate
python manage.py runserver

### Layout ui - streamlit
cd ~/archelp
source .venv/bin/activate
streamlit run ui/app.py

### Cloudflare
cloudflared tunnel run archelp

## Link
### Dashboard
http://localhost:8000/admin
### Api
http://localhost:8000/api/docs
### UI - Streamlit
http://localhost:8501/

## Tmux
cd ~/archelp
tmux new -s archelp



tmux attach -t archelp     # quay lại