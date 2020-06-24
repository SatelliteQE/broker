FROM fedora
MAINTAINER https://github.com/SatelliteQE

RUN dnf -y install make cmake gcc-c++ zlib-devel \
           openssl-devel git python3-pip python3-devel \
           && dnf clean all

WORKDIR /home/broker
COPY . /home/broker/
RUN pip install .
RUN cp settings.yaml.example settings.yaml
RUN chmod -Rv 0777 /home/broker
USER 1001

ENTRYPOINT ["broker"]
CMD ["--help"]
