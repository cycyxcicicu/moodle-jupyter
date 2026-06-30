import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from services.exam_worker import run_auto_submit_check
from routers import pages, student, teacher, internal

app = FastAPI(title="Jupyter Assignment Service")

# Cho phép CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Đăng ký các router
app.include_router(pages.router)
app.include_router(student.router, prefix="/services/assignment-service/api/student")
app.include_router(teacher.router, prefix="/services/assignment-service/api/teacher")
app.include_router(internal.router, prefix="/services/assignment-service/api/internal")

@app.on_event("startup")
async def startup_event():
    # Khởi tạo cấu trúc cơ sở dữ liệu và chạy migrations
    init_db()
    
    # Khởi chạy tác vụ kiểm tra tự động nộp bài chạy ngầm (background worker)
    async def worker_loop():
        await asyncio.sleep(5)
        while True:
            try:
                await run_auto_submit_check()
            except Exception as e:
                print(f"Error in background auto-submit worker: {e}", flush=True)
            await asyncio.sleep(10)
            
    asyncio.create_task(worker_loop())

    # Khởi chạy tác vụ background worker cho hàng đợi đăng ký học viên (enrollment queue)
    async def enrollment_worker_loop():
        await asyncio.sleep(5)
        while True:
            try:
                await internal.run_enrollment_queue_worker()
            except Exception as e:
                print(f"Error in background enrollment queue worker: {e}", flush=True)
            await asyncio.sleep(300)

    asyncio.create_task(enrollment_worker_loop())
    print("[Startup] Enrollment queue worker đã được khởi chạy.", flush=True)
