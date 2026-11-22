ssh -i ~/.ssh/key.pem ubuntu@195.209.223.167
cd app
sudo docker compose pull gen php nginx
sudo docker compose up -d --no-build gen php nginx
sudo docker image prune -f