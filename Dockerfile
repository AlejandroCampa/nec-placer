FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential git cmake libxml2-dev wget \
    && rm -rf /var/lib/apt/lists/*

# Build libredwg from source
RUN git clone --depth 1 https://github.com/LibreDWG/libredwg.git /tmp/libredwg \
    && cd /tmp/libredwg \
    && apt-get update && apt-get install -y autoconf automake libtool \
    && sh autogen.sh \
    && ./configure \
    && make -j4 \
    && make install \
    && ldconfig \
    && rm -rf /tmp/libredwg

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]
