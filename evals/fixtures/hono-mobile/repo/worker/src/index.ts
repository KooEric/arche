import { Hono } from "hono";

import { orders } from "./routes/orders";
import { verifyToken } from "./session";

const app = new Hono();

app.get("/catalog/version", (context) => context.json({ version: 1 }));
app.post("/auth/kakao", (context) => context.json({}));
app.post("/auth/google", (context) => context.json({}));
app.use("/orders/*", verifyToken);
app.route("/orders", orders);

export default app;
