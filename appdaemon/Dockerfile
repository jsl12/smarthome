FROM python:3.8.8

EXPOSE 5050

ENV CRYPTOGRAPHY_DONT_BUILD_RUST=1
RUN pip install appdaemon

COPY ./requirements.txt /conf/
RUN pip install -r /conf/requirements.txt
