# =============================================================
# 红楼智问 · RAG 后端镜像
# =============================================================
# 基础镜像复用 hadoop-lab:2026.10 —— 它已含
#   Python 3.10 / happybase / onnxruntime / numpy / hadoop 客户端配置
# 这样后端可以直接用 HDFS CLI、连 HBase Thrift，无需重装依赖。
# =============================================================
FROM hadoop-lab:2026.10

WORKDIR /app

# FastAPI + httpx + onnxruntime
# 注意：onnxruntime 必须显式装进镜像。
# hadoop-lab:2026.10 里虽然有 numpy/pandas/happybase，
# 但 onnxruntime 是上一轮在**运行中的容器**里 pip install 的，
# 属于容器可写层，不在镜像层 —— 重新 run 就没了（实测踩过：
# ModuleNotFoundError: No module named 'onnxruntime'）。
RUN pip3 install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        fastapi==0.115.6 \
        uvicorn[standard]==0.34.0 \
        httpx==0.28.1 \
        pydantic==2.10.4 \
        onnxruntime==1.23.2

# 复制后端代码
COPY app.py /app/app.py

# 复制 embedding 模型进镜像
# 为什么不挂载宿主目录：模型在 master 容器的 /data 命名卷里，
# hlm-api 是另一个容器，看不到那个卷（实测 FileNotFoundError）。
# 打镜像里最省事，也让后端可独立移植。
COPY chapter_meta.json /app/chapter_meta.json
# 人物身份聚合表（S20：全集型问题用，如「宝玉的丫鬟有谁」）
COPY person_roles.json /app/person_roles.json
# S35：示例问题 + 预热清单（共享数据源，前端也读同一份）
COPY starters.json /app/starters.json
# S21 自定义查询：语料 API + 沙箱
COPY query_api.py /app/query_api.py
COPY sandbox.py /app/sandbox.py
# S24 Hadoop 能力 API（自定义查询真正访问 HDFS/HBase）
COPY hadoop_api.py /app/hadoop_api.py
# 批语文本（供 load_pizhu 使用）
COPY pizhu_fuqinju.json /app/pizhu_fuqinju.json
COPY pizhu_guiyouben.json /app/pizhu_guiyouben.json
COPY stats_summary.json /app/stats_summary.json
COPY models/bge-small-zh-v1.5 /app/models/bge-small-zh-v1.5
ENV HLM_MODEL_DIR=/app/models/bge-small-zh-v1.5

# 问答日志目录（挂载点）
RUN mkdir -p /workspace/pipeline/out/qa_logs

EXPOSE 8000

# 注意：必须覆盖 entrypoint，否则会继承集群的 cluster-entrypoint
# （它会等待 master 健康检查，作为独立服务会一直挂起）
ENTRYPOINT ["python3", "-m", "uvicorn"]
CMD ["app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
