FROM python:latest

# Environment vars we can configure against
# But these are optional, so we won't define them now
#ENV HA_URL http://hass:8123
#ENV HA_KEY secret_key
#ENV DASH_URL http://hass:5050
#ENV EXTRA_CMD -D DEBUG

# API Port
EXPOSE 5050
EXPOSE 80
EXPOSE 443

# Mountpoints for configuration & certificates
VOLUME /conf
VOLUME /certs

# # Copy appdaemon into image
# WORKDIR /usr/src/app
# COPY . .

ENV CRYPTOGRAPHY_DONT_BUILD_RUST=1

RUN pip install appdaemon

COPY ./appdaemon.yaml /conf/

RUN chmod +x /usr/src/app/dockerStart.sh
ENTRYPOINT ["./dockerStart.sh"]
