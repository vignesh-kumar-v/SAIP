import "./styles.css";
import * as Tooltip from "@radix-ui/react-tooltip";
import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <Tooltip.Provider>
        <App />
      </Tooltip.Provider>
    </MotionConfig>
  </StrictMode>,
);
