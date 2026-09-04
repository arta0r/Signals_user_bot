# Optional: run the loop on Hugging Face Spaces (free, exact schedule, private code)
# instead of GitHub Actions. In the Space settings pick "Docker" as SDK.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# scan.py --loop polls every N minutes; default 10
ENV EVERY_MINUTES=10
EXPOSE 7860

# a trivial health endpoint so the Space does not sleep for "no traffic":
# python http.server on :7860 in parallel with the scanner
CMD ["sh", "-c", "python -m http.server 7860 --directory /app/out & exec python scan.py --loop --every-minutes ${EVERY_MINUTES}"]
