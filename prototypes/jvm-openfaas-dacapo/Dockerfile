ARG BASE_IMAGE=maheshk523/hivejit-openfaas-test:fix13
FROM ${BASE_IMAGE}

WORKDIR /app

RUN mkdir -p /app/lib

COPY --from=dacapo_payload dacapo.jar /app/lib/dacapo.jar
COPY --from=dacapo_payload dacapo /app/lib/dacapo
COPY S.java /tmp/S.java
COPY bootstrap.sh /app/bootstrap.sh

RUN set -eux; \
    mkdir -p /tmp/function-classes /tmp/dacapo-log /tmp/dacapo-scratch; \
    javac -cp /app/lib/dacapo.jar -d /tmp/function-classes /tmp/S.java; \
    jar --create --file /app/function.jar -C /tmp/function-classes .; \
    chmod +x /app/bootstrap.sh; \
    rm -rf /tmp/function-classes /tmp/S.java

ENV DACAPO_ALLOWED_BENCHMARKS=h2,lusearch,eclipse,fop,jython
ENV DACAPO_BENCHMARK=lusearch
ENV DACAPO_SIZE=small
ENV DACAPO_ITERATIONS=1
ENV DACAPO_THREADS=1
ENV DACAPO_PRE_GC=true
ENV DACAPO_DIGEST_OUTPUT=false
ENV DACAPO_VALIDATION=none
ENV DACAPO_LOG_DIR=/tmp/dacapo-log
ENV DACAPO_SCRATCH=/tmp/dacapo-scratch
ENV read_timeout=300s
ENV write_timeout=300s
ENV exec_timeout=300s

EXPOSE 8080
CMD ["/app/bootstrap.sh"]
