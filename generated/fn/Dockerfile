FROM eclipse-temurin:21-jdk AS build
WORKDIR /build
COPY src ./src
RUN javac -d out $(find src -name '*.java') && jar --create --file function.jar -C out .

FROM eclipse-temurin:21-jdk
WORKDIR /app
COPY --from=build /build/function.jar /app/function.jar
COPY lib /app/lib
COPY george-jdk /opt/george-jdk
COPY bootstrap.sh /app/bootstrap.sh
RUN chmod +x /app/bootstrap.sh
ENV ARTIFACT_KEY=__ARTIFACT_KEY__
EXPOSE 8080
CMD ["/app/bootstrap.sh"]
