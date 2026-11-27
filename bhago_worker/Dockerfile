FROM node:22-bookworm-slim
WORKDIR /opt/bhago
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv tini && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY bhago_worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY bhago_worker/package.json ./package.json
RUN npm install --omit=dev --ignore-scripts
RUN npx playwright install --with-deps chromium
RUN node -e "(async()=>{const b=await require('playwright').chromium.launch({channel:'chromium',headless:true,args:['--disable-dev-shm-usage']});await b.close();})().catch(e=>{console.error(e);process.exit(1)})"
COPY bhago_worker/ ./
ENV PYTHONUNBUFFERED=1 BHAGO_RUNTIME=cloud BHAGO_PRIVATE_DIR=/data/private BHAGO_OUTPUT_DIR=/data/uploads BHAGO_PLAYWRIGHT_MODULE=playwright BHAGO_GOOGLE_OAUTH_ENABLED=true BHAGO_SCHEDULE_ENABLED=false BHAGO_RESULTS_ENABLED=true BHAGO_BACKLOG_ENABLED=true
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["python", "-u", "cloud_start.py"]
