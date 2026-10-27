FROM alpine:3.20
RUN mkdir -p /secret
COPY exercise_secret.txt /secret/exercise_secret.txt
COPY signin.sh /usr/local/bin/signin
COPY rotate-signin.sh /usr/local/bin/rotate-signin
COPY reset-signin.sh /usr/local/bin/reset-signin
COPY read-file.sh /usr/local/bin/read-file
COPY list-files.sh /usr/local/bin/list-files
RUN chmod +x /usr/local/bin/signin /usr/local/bin/rotate-signin /usr/local/bin/reset-signin /usr/local/bin/read-file /usr/local/bin/list-files \
    && printf 'dbops' > /secret/.toy_user \
    && printf 'dbops-fake-19' > /secret/.toy_pass
CMD ["sleep", "infinity"]
