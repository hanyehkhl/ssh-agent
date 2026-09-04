# Disposable target box for ssh-agent acceptance scenarios.
# Ubuntu + sshd + nginx, matching the plan's verification section.
FROM ubuntu:22.04

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        openssh-server nginx sudo curl procps iproute2 net-tools && \
    mkdir -p /var/run/sshd

RUN useradd -m -s /bin/bash agentuser && \
    echo "agentuser:agentpass" | chpasswd && \
    usermod -aG sudo agentuser && \
    echo "agentuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

RUN sed -i 's/#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# The scenario scripts are baked in so a breakage can be triggered inside a
# running box with `docker exec ssh-agent-test /opt/break_nginx.sh`.
COPY break_nginx.sh fill_disk.sh /opt/
RUN chmod +x /opt/break_nginx.sh /opt/fill_disk.sh

EXPOSE 22
CMD service nginx start && /usr/sbin/sshd -D
