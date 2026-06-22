import uvicorn
import os
from dotenv import load_dotenv
load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.web.routes:app", host="0.0.0.0", port=port, reload=False)
