FROM alpine:3.20
RUN mkdir -p /secret /srv/app
COPY config.ini /srv/app/config.ini
COPY signin.sh /usr/local/bin/signin
COPY rotate-signin.sh /usr/local/bin/rotate-signin
COPY reset-signin.sh /usr/local/bin/reset-signin
COPY read-file.sh /usr/local/bin/read-file
COPY list-files.sh /usr/local/bin/list-files
RUN chmod +x /usr/local/bin/signin /usr/local/bin/rotate-signin /usr/local/bin/reset-signin /usr/local/bin/read-file /usr/local/bin/list-files \
    && printf 'svc-report' > /secret/.toy_user \
    && printf 'password' > /secret/.toy_pass
CMD ["sleep", "infinity"]
