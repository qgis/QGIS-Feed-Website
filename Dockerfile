# For more information, please refer to https://aka.ms/vscode-docker-python
FROM python:3.12-slim

EXPOSE 8000

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

RUN apt-get update
RUN echo "Installing GDAL dependencies" && \
    apt-get install -y libgdal-dev libcurl4-gnutls-dev librtmp-dev && \
    echo "Installing libpq for psycopg2 (built from source, no wheels published)" && \
    apt-get install -y libpq-dev && \
    echo "Installing other depdencies" && \
    apt-get install -y wait-for-it curl sudo && \
    echo "Install C library for geoip2" && \
    apt-get install -y libmaxminddb0 libmaxminddb-dev mmdb-bin && \
    echo "Removing build dependencies and cleaning up" && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf ~/.cache/pip

# Install pip requirements
ADD REQUIREMENTS.txt .
RUN python -m pip install -r REQUIREMENTS.txt

RUN apt-get update && apt-get install -y curl && curl -LJO https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb && \
    mkdir /var/opt/maxmind && \
    mv GeoLite2-City.mmdb /var/opt/maxmind/GeoLite2-City.mmdb

# Install NodeJS and bulma css webpack
RUN apt-get -qq update && apt-get -qq install -y --no-install-recommends wget && \
    wget --no-check-certificate https://deb.nodesource.com/setup_22.x -O /tmp/node.sh && bash /tmp/node.sh && \
    apt-get -qq update && apt-get -qq install -y nodejs build-essential

WORKDIR /code
COPY . /code
RUN npm install -g npm@10.2.1 && npm install -g webpack@5.89.0 && npm install -g webpack-cli@5.1.4 && npm install

ENV GEOIP_PATH=/var/opt/maxmind/


# Creates a non-root user
# During debugging, this entry point will be overridden. For more information, please refer to https://aka.ms/vscode-docker-python-debug
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "qgisfeedproject.wsgi"]
