import { Hono } from "hono";

export const orders = new Hono();

orders.get("/", (context) => context.json([]));
orders.get("/:id", (context) => context.json({ id: context.req.param("id") }));
