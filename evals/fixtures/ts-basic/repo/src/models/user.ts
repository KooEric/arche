export interface User {
  id: string;
  role: Role;
}

export type UserId = string;

export enum Role {
  Admin,
  Member,
}
