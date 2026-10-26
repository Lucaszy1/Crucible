FROM python:3.12-alpine
# Third node for lateral movement. Plain Python plus a few dummy files.
RUN mkdir -p /srv/data /srv/state \
    && echo "quarterly report draft (dummy text)" > /srv/data/report.txt \
    && echo "team lunch notes (dummy text)" > /srv/data/notes.txt \
    && echo "clean" > /srv/state/integrity.flag
CMD ["sleep", "infinity"]
