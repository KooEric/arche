import React from 'react';
import './button.css';
import { level } from '../lib/logger';

export const Button = () => <button className="b" title={level}>Don't click</button>;

export default Button;
