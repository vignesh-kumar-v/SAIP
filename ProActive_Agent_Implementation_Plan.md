# ProActive Agent Implementation Roadmap

## Introduction
The goal of this project is to build a proactive agent that understands when to interrupt based on whether the user is in a "flow" state or a "stuck" state. We will build this in five phases, starting from gathering reliable data to deploying a dynamic, threshold-based LLM.

## Phase 1: Data Collection & Ground Truth
**The Intuition:** Before we can teach an AI when to interrupt, we need to know what being "stuck" or "in flow" looks like in the data. We need to collect background activity and match it to moments when someone actually reaches out for help. 

**Detailed Execution:**
*   **Set up telemetry loggers:** Install background trackers like ActivityWatcher or LifeTrace. We need to capture window switches, typing burst rates (not the actual keys, just the volume), and terminal errors.
*   **Capture natural help-seeking:** Instead of relying on a custom hotkey (which breaks natural habits), log the exact timestamps when a user voluntarily opens an AI chatbot for help. 
*   **Validate signals against ground truth:** Compare the background telemetry logs against the moments users sought help and your manual screenshot labels. If the logs don't show a clear pattern before a help request, the signals need adjustment.

## Phase 2: The State Detector
**The Intuition:** Now that we have data, we need a fast, cheap way to read it continuously. We don't want to run a heavy AI model every second. Instead, we build a lightweight system that looks at the recent past and gives a simple score from 0 (flow) to 1 (stuck).

**Detailed Execution:**
*   **Build the time-based rolling window:** Implement a lookback window (e.g., the last 60 seconds) rather than just counting the last 3-4 events. This provides better context for mixed activities.
*   **Develop the lightweight classifier:** Create a non-LLM algorithm that processes the rolling window. High error rates and repeated window toggles push the score closer to 1. Steady typing keeps it near 0.
*   **Implement distraction handling and smoothing:** A sudden pause shouldn't instantly spike the stuck score. Use an Exponential Moving Average (EMA) to smooth the score over time. If there is no mouse or keyboard activity, pause the scoring so the agent doesn't confuse a coffee break with being stuck.

## Phase 3: LLM & Dynamic Thresholds
**The Intuition:** Once the lightweight detector thinks you might be stuck, we bring in the "brains" (the LLM) to make a final check. We also dynamically adjust the cost of interrupting you based on your current state.

**Detailed Execution:**
*   **Deploy the local LLM:** Set up your chosen local model (hosted on GCP, AWS, or local hardware) with a fast API endpoint.
*   **Write the yes/no confidence prompt:** Feed the recent context to the LLM and ask one strict question: "Does the user need help now?". Extract the model's calibrated probability (confidence) of outputting a "yes".
*   **Code the dynamic PRISM threshold:** Adapt the threshold formula from the PRISM paper. Make the cost of interruption inversely proportional to the stuck score from Phase 2. High stuck score = low threshold to interrupt. Low stuck score = high threshold to interrupt.

## Phase 4: Orchestration & UI
**The Intuition:** This is where we wire everything together. The system monitors your state, checks the threshold, and decides if it should actually pop up and speak.

**Detailed Execution:**
*   **Wire the trigger logic:** The LLM is only called when the lightweight classifier's stuck score crosses a baseline threshold. 
*   **Implement the decision matrix:** 
    *   *If LLM Confidence > Dynamic Threshold:* The agent intervenes.
    *   *If borderline but state is Flow:* Wait for a natural break.
    *   *Otherwise:* Stay quiet.
*   **Build the intervention UI:** Create the actual chat interface or notification system that safely interrupts the user without taking over their screen aggressively.

## Phase 5: End-to-End Testing
**The Intuition:** The system is built, but it needs to be tested in the real world to see if the timing actually feels natural.

**Detailed Execution:**
*   **Run a live dogfooding session:** Use the system yourself during your daily work for a few days to experience the interventions firsthand.
*   **Tune sensitivity and EMA:** Adjust the smoothing functions and the cost weights based on your experience. If it interrupts your flow too often (false positives), raise the baseline cost. If it stays silent when you are frustrated (false negatives), lower it.
