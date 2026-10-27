# Config-driven toy host for generated scenarios. One image plays any
# host in any spec: the entrypoint reads the mounted spec JSON plus
# TOYHOST_ID. Python slim, stdlib only, no extra dependencies.
FROM python:3.12-slim
RUN mkdir -p /app /run/toyhost
COPY toyhost.py /app/toyhost.py
COPY signin.sh /usr/local/bin/signin
COPY rotate-signin.sh /usr/local/bin/rotate-signin
COPY reset-signin.sh /usr/local/bin/reset-signin
COPY read-file.sh /usr/local/bin/read-file
COPY list-files.sh /usr/local/bin/list-files
COPY ps.sh /usr/local/bin/ps
RUN chmod +x /usr/local/bin/signin /usr/local/bin/rotate-signin \
    /usr/local/bin/reset-signin /usr/local/bin/read-file \
    /usr/local/bin/list-files /usr/local/bin/ps
CMD ["python", "/app/toyhost.py"]
