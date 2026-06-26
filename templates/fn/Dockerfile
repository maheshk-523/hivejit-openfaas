FROM eclipse-temurin:21-jdk AS build
WORKDIR /build
COPY src ./src
RUN javac -d out $(find src -name '*.java') && \
    jar --create --file function.jar -C out .

FROM eclipse-temurin:21-jdk
WORKDIR /app
COPY --from=build /build/function.jar /app/function.jar
COPY george-jdk /opt/george-jdk
COPY bootstrap.sh /app/bootstrap.sh
RUN chmod +x /app/bootstrap.sh
ENV PROFILE_PATH=/profiles/latest.mdox
ENV PROFILE_DUMP_PATH=/profiles/out.mdox
ENV ARTIFACT_KEY=__ARTIFACT_KEY__
ENV REDIS_HOST=redis.jit-system.svc.cluster.local
ENV REDIS_PORT=6379
ENV PORT=8080
CMD ["/app/bootstrap.sh"]
