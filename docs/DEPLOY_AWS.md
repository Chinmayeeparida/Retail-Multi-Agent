# Deploy on AWS (Amazon Lightsail, one small server)

All four apps run from **one Docker image** on **one small server**, so they share one database (the request-to-report chain needs that).
Written from the AWS documentation; button names on the AWS website can differ slightly.

## What it costs (check the current prices before you start)
* Lightsail **2 GB** server (enough for the four apps): **US$12 per month**, billed per hour and capped at the monthly price, so a few days cost a few dollars at most. A 4 GB server is US$24 per month.
* A static IP address is free while it is attached to a server; delete it when you delete the server.
* A new AWS account may get free credits. Check the **Billing** page to see whether they cover Lightsail.

## Rules for a public server
1. **Demo data only.** Never put real company data, API keys or e-mail passwords on it.
2. The apps have **no login**. Anyone who finds the address can use them, so in step 5 allow **only your own IP address**.
3. **Delete the server when you are done** (last section) and look at the Billing page the next day.

## Steps
1. **Create an AWS account** at aws.amazon.com (a card is needed; you type it on the AWS website yourself). Sign in to the console.
2. **Set a budget alert.** Search "Budgets" in the top search box, open **Billing and Cost Management > Budgets > Create budget**, choose a monthly cost budget of **US$15** and add your e-mail for alerts.
3. **Create the server.** Open **lightsail.aws.amazon.com > Create instance**.
   * Region: a place near you (for India, Mumbai).
   * Platform: **Linux/Unix**, blueprint: **OS Only > Ubuntu 24.04 LTS**.
   * Plan: the **US$12, 2 GB** plan. Name: `retail-demo`. Click **Create instance**.
4. **Give it a fixed address.** Open the instance, tab **Networking > Create static IP > attach to retail-demo**. Write the IP down (for example `203.0.113.7`).
5. **Open the four app ports, for you only.** Same tab, **IPv4 Firewall > Add rule**: Application *Custom*, Protocol *TCP*, Port range `8501-8504`, tick **Restrict to IP address** and add your own public IP (search "what is my IP" on Google). Save.
6. **Open a terminal on the server.** On the instance page click **Connect using SSH** (a browser terminal opens).
7. **Install the tools** (copy the lines one by one):
   ```bash
   sudo apt-get update
   sudo apt-get install -y docker.io docker-compose-v2 git
   ```
8. **Get the project and start it** (replace the IP with yours):
   ```bash
   git clone https://github.com/Chinmayeeparida/Retail-Multi-Agent.git
   cd Retail-Multi-Agent
   echo "PUBLIC_HOST=203.0.113.7" > .env
   sudo docker compose up -d --build
   ```
   The first build takes a few minutes. The first start then invents the demo data and builds the database (about a minute more).
9. **Open the apps** in your browser:
   * 360 Degree View: `http://203.0.113.7:8501`
   * Store Replenishment App: `http://203.0.113.7:8502`
   * Warehouse Packaging App: `http://203.0.113.7:8503`
   * Store Stock Request App: `http://203.0.113.7:8504`

## Everyday commands (in the server terminal, inside `Retail-Multi-Agent`)
| What | Command |
|---|---|
| See if it is running | `sudo docker compose ps` |
| Read the messages | `sudo docker compose logs -f` (Ctrl + C to leave) |
| Update after a change on GitHub | `git pull && sudo docker compose up -d --build` |
| Stop (database is kept) | `sudo docker compose down` |
| Start from fresh demo data | `sudo docker compose down -v && sudo docker compose up -d` |

Optional: to use the AI-written notes, add `GOOGLE_API_KEY=your-key` as a new line in the server's `.env` file (type it on the server; never put it on GitHub), then run the `up -d --build` command again.

## If something does not work
* **The page does not load.** Your home IP address may have changed: update the firewall rule (step 5). Or wait one minute after the start; the database is being built.
* **The server is very slow or an app stops.** It ran out of memory: in Lightsail choose **Manage > Change plan** and take the 4 GB plan.
* **Times look wrong.** The time zone is set to `Asia/Kolkata`; change `TZ` in `docker-compose.yml`.

## Delete everything when you are done (stops the billing)
1. Lightsail > your instance > **Delete instance**.
2. **Networking > Static IPs > delete the static IP** (an unattached one is charged).
3. Look at **Billing** the next day to confirm nothing is running.
