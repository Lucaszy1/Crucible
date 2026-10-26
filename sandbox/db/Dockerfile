FROM alpine:3.20
RUN mkdir -p /secret
COPY exercise_secret.txt /secret/exercise_secret.txt
COPY signin.sh /usr/local/bin/signin
COPY rotate-signin.sh /usr/local/bin/rotate-signin
COPY reset-signin.sh /usr/local/bin/reset-signin
RUN chmod +x /usr/local/bin/signin /usr/local/bin/rotate-signin /usr/local/bin/reset-signin \
    && printf 'admin' > /secret/.toy_user \
    && printf 'changeme' > /secret/.toy_pass
CMD ["sleep", "infinity"]
