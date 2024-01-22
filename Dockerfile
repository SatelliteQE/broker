FROM fedora
MAINTAINER https://github.com/SatelliteQE

RUN dnf -y install make cmake gcc-c++ zlib-devel \
           openssl-devel git python3-pip python3-devel \
           && dnf clean all
WORKDIR /root/broker
COPY . /root/broker/

RUN pip install .

ENTRYPOINT ["broker"]
CMD ["--help"]
