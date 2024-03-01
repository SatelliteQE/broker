FROM fedora
MAINTAINER https://github.com/SatelliteQE

ENV PWD /root/broker

RUN dnf -y install make cmake gcc-c++ zlib-devel \
           openssl-devel git python3-pip python3-devel which\
           && dnf clean all


WORKDIR $PWD
COPY . $PWD

RUN pip install uv
RUN uv pip install -e .

ENTRYPOINT ["broker"]
CMD ["--help"]
