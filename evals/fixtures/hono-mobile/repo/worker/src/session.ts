export const verifyToken = async (context: any, next: () => Promise<void>) => {
  const response = await fetch("https://id.example.com/oauth/tokeninfo", {
    method: "POST",
    body: context.req.header("authorization"),
  });
  if (response.ok) await next();
};
