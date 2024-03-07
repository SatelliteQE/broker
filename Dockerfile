FROM fedora
MAINTAINER https://github.com/SatelliteQE

RUN dnf -y install make cmake gcc-c++ zlib-devel \
           openssl-devel git python3-pip python3-devel which\
           && dnf clean all


WORKDIR /root/broker
COPY . /root/broker

RUN pip install uv
RUN uv venv && source .venv/bin/activate
RUN uv pip install -e .

ENTRYPOINT ["broker"]
CMD ["--help"]
