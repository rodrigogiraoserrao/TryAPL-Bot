# syntax=docker/dockerfile:1

FROM python:3.8-slim-buster

WORKDIR /bot

COPY requirements.txt requirements.txt
RUN python -m pip install -r requirements.txt

# Run as less as possible as root.
RUN useradd apprunner
# Make `apprunner` the owner of the working dir so the script is allowed to write files.
RUN chown apprunner .
USER apprunner

COPY resources/Apl385.ttf resources/Apl385.ttf
COPY main.py main.py

CMD ["python", "main.py"]
