FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential git cmake libxml2-dev wget \
    autoconf automake libtool texinfo \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/LibreDWG/libredwg.git /tmp/libredwg \
    && cd /tmp/libredwg \
    && sh autogen.sh \
    && ./configure --prefix=/usr \
    && make -j4 \
    && make install \
    && ldconfig \
    && rm -rf /tmp/libredwg

# Verify dwg2dxf is installed - build fails here if not found
RUN which dwg2dxf && echo "dwg2dxf OK"

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]
