# Detection job image. Build from the repository root (the job generates its
# Protobuf classes from schemas/events/v2):
#   docker build -f flink/detection-job/Dockerfile .
FROM docker.io/library/eclipse-temurin:17.0.16_8-jdk AS build
WORKDIR /src
COPY schemas/events/v2 schemas/events/v2
COPY flink/detection-job/.mvn flink/detection-job/.mvn
COPY flink/detection-job/mvnw flink/detection-job/pom.xml flink/detection-job/
RUN cd flink/detection-job && ./mvnw -B -q dependency:go-offline
COPY flink/detection-job/src flink/detection-job/src
RUN cd flink/detection-job && ./mvnw -B -q package -DskipTests

FROM docker.io/library/flink:1.20.5-scala_2.12-java17
COPY --from=build /src/flink/detection-job/target/detection-job-0.1.0.jar /opt/flink/usrlib/detection-job.jar
