FROM fedora
MAINTAINER https://github.com/SatelliteQE

RUN dnf -y install make cmake gcc-c++ zlib-devel \
           openssl-devel git python3 python3-pip python3-devel which\
           && dnf clean all


WORKDIR /root/broker
COPY . /root/broker

RUN pip install uv
RUN uv pip install --system "broker @ ." 

ENTRYPOINT ["broker"]
CMD ["--help"]
