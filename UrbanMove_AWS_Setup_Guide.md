

nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
n
UrbanMove — Cloud Native Smart Mobility Platform
AWS Console Setup Guide
Follow in this exact order. Takes ~2.5 hours.
nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
n
STEP 1 — VPC Setup (us-east-1)
- Go to VPC → Create VPC
Name:     urbanmove-vpc
IPv4 CIDR: 10.0.0.0/16
Click "Create VPC"
- Create 4 Subnets inside urbanmove-vpc:
urbanmove-public-1a → 10.0.1.0/24 → AZ: us-east-1a
urbanmove-public-1b → 10.0.2.0/24 → AZ: us-east-1b
urbanmove-private-1a → 10.0.3.0/24 → AZ: us-east-1a
urbanmove-private-1b → 10.0.4.0/24 → AZ: us-east-1b
## 3. Create Internet Gateway
Name: urbanmove-igw → Attach to urbanmove-vpc
- Create NAT Gateway (for private subnet outbound access)
Subnet: urbanmove-public-1a | Elastic IP: Allocate new
## 5. Create Route Tables
Public RT → urbanmove-public-rt
Route: 0.0.0.0/0 → urbanmove-igw
Associate: urbanmove-public-1a, urbanmove-public-1b
Private RT → urbanmove-private-rt
Route: 0.0.0.0/0 → NAT Gateway
Associate: urbanmove-private-1a, urbanmove-private-1b
STEP 2 — Security Groups
Create these 5 SGs inside urbanmove-vpc:
SG 1: urbanmove-alb-sg
Inbound:  HTTP  80    from 0.0.0.0/0
HTTPS 443   from 0.0.0.0/0
Outbound: All traffic
SG 2: urbanmove-ec2-sg
Inbound:  Custom TCP 5000  from urbanmove-alb-sg (SG ID)
Custom TCP 8080  from urbanmove-alb-sg (SG ID)

SSH       22     from YOUR_IP/32
Outbound: All traffic
SG 3: urbanmove-rds-sg
Inbound:  PostgreSQL 5432  from urbanmove-ec2-sg (SG ID)
Outbound: All traffic
SG 4: urbanmove-kafka-sg
Inbound:  Custom TCP 9092  from urbanmove-ec2-sg (SG ID)
Custom TCP 2181  from urbanmove-ec2-sg (SG ID)
Outbound: All traffic
SG 5: urbanmove-influx-sg
Inbound:  Custom TCP 8086  from urbanmove-ec2-sg (SG ID)
Outbound: All traffic
STEP 3 — RDS PostgreSQL (User & Config Data)
- Go to RDS → Create Database
Engine: PostgreSQL 15
Template: Free tier
DB identifier: urbanmove-db
Master username: admin
Master password: UrbanDB!Secure2025 ← save this!
Instance: db.t3.micro
Storage: 20 GB gp2
VPC: urbanmove-vpc
Subnet group: Create new → select both PRIVATE subnets
Public access: NO
Security group: urbanmove-rds-sg
Initial DB name: urbanmove
- Wait ~10 min for it to start
- Copy the Endpoint → paste into .env as DB_HOST
STEP 4 — ElastiCache Redis (Session & Cache)
- Go to ElastiCache → Create cluster
Engine: Redis OSS
Name: urbanmove-cache
Node type: cache.t3.micro
## Replicas: 0 (demo)
VPC: urbanmove-vpc
Subnet group: Create new → select both PRIVATE subnets
Security group: urbanmove-ec2-sg
- Copy Primary Endpoint → paste into .env as REDIS_HOST

STEP 5 — Amazon MSK (Kafka) — Event Streaming Bus
- Go to MSK → Create cluster
Cluster name: urbanmove-kafka
Creation: Quick create
Broker type: kafka.t3.small
Brokers: 1 per AZ (2 total for demo)
VPC: urbanmove-vpc
Subnets: urbanmove-private-1a, urbanmove-private-1b
Security group: urbanmove-kafka-sg
- Wait ~15 min for cluster to provision
- Go to cluster → View client information
Copy Bootstrap servers → paste into .env as KAFKA_BROKERS
- Create these Kafka Topics:
urbanmove.gps.events    (partitions: 6, retention: 24h)
urbanmove.sensor.events (partitions: 6, retention: 24h)
urbanmove.congestion    (partitions: 3, retention: 1h)
urbanmove.routes        (partitions: 3, retention: 6h)
urbanmove.alerts        (partitions: 3, retention: 12h)
CLI example (run from an EC2 in the same VPC):
kafka-topics.sh --create \
--bootstrap-server $KAFKA_BROKERS \
--topic urbanmove.gps.events \
## --partitions 6 --replication-factor 2
STEP 6 — S3 Buckets
- Create raw mobility data bucket
Name: urbanmove-mobility-data-YOURNAME
Region: us-east-1
Block all public access: YES
## Versioning: Enable
- Create analytics output bucket
Name: urbanmove-analytics-YOURNAME
Region: us-east-1
Block all public access: YES
- Copy bucket names → paste into .env:
S3_RAW_BUCKET=urbanmove-mobility-data-YOURNAME
S3_ANALYTICS_BUCKET=urbanmove-analytics-YOURNAME
- For DR Cross-Region Replication (do after DR EC2 is up):
Go to bucket → Management → Replication rules
Create rule → Replicate to new bucket in us-west-2

DR bucket name: urbanmove-mobility-dr-YOURNAME
STEP 7 — Cognito User Pool (Auth Service)
- Go to Cognito → Create User Pool
Name: urbanmove-users
## Sign-in: Email
Password: 8+ chars, 1 uppercase, 1 number, 1 symbol
MFA: OFF (for demo)
Email: Send via Cognito (free)
App client name: urbanmove-app
Auth flow: ALLOW_USER_PASSWORD_AUTH ← CRITICAL, must enable
- Copy User Pool ID → COGNITO_POOL_ID in .env
- Copy App Client ID → COGNITO_CLIENT_ID in .env
- Add custom attributes:
custom:role  (String, mutable) → values: admin | operator | user
custom:city  (String, mutable) → e.g. Paris, Lyon
STEP 8 — IAM Role for EC2
- Go to IAM → Roles → Create Role
Trusted entity: EC2
Name: urbanmove-ec2-role
Add these policies:
AmazonS3FullAccess
AmazonCognitoPowerUser
CloudWatchAgentServerPolicy
AmazonMSKFullAccess
AmazonSNSFullAccess
STEP 9 — EC2 Instances (2 App Servers + 1 Stream Processor)
App servers — create 2, one per AZ
For EACH app instance:
- Go to EC2 → Launch Instance
Name: urbanmove-app-1a (urbanmove-app-1b for second)
AMI: Amazon Linux 2023
Type: t2.micro (free tier)
Key pair: Create new → urbanmove-key → download .pem
VPC: urbanmove-vpc
Subnet: urbanmove-public-1a (1b for second)
Auto-assign IP: Enable
Security group: urbanmove-ec2-sg
IAM role: urbanmove-ec2-role

Storage: 8 GB gp2
After launch — SSH into each and run:
chmod 400 urbanmove-key.pem
ssh -i urbanmove-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
Upload project files via SCP:
scp -i urbanmove-key.pem -r ./urbanmove ec2-user@EC2_IP:/home/ec2-user/
On EC2, initialize and start:
cd /home/ec2-user/urbanmove
nano .env                   ← fill in all real values
psql -h $DB_HOST -U admin -d urbanmove < schema.sql
bash setup_ec2.sh           ← installs Node/Python, starts services
Stream Processor EC2 (1 instance, private subnet)
Name: urbanmove-stream-processor
AMI: Amazon Linux 2023
Type: t2.micro
Subnet: urbanmove-private-1a ← private, no public IP
Security: urbanmove-kafka-sg
IAM role: urbanmove-ec2-role
Upload and run the processor:
bash setup_processor.sh   ← installs Python + Kafka client
python3 consumer.py       ← consumes urbanmove.gps.events
STEP 10 — Application Load Balancer
- Go to EC2 → Load Balancers → Create
## Type: Application Load Balancer
Name: urbanmove-alb
## Scheme: Internet-facing
VPC: urbanmove-vpc
Subnets: urbanmove-public-1a AND urbanmove-public-1b (BOTH!)
SG: urbanmove-alb-sg
- Create Target Group first:
Name: urbanmove-tg
## Type: Instances
Protocol: HTTP, Port: 5000
Health check: /health
Register both app EC2 instances
- Listener: HTTP:80 → Forward to urbanmove-tg
- Copy ALB DNS name → your app URL for the demo

STEP 11 — CloudWatch Monitoring & Alarms
- Go to CloudWatch → Dashboards → Create dashboard
Name: UrbanMove-Ops
- Add these widgets:
- EC2 CPU Utilization (both instances)
- ALB Request Count
- ALB Target Response Time
- RDS CPU & Connections
- MSK Kafka Bytes In/Out
## 3. Create Alarms:
Alarm 1: EC2 CPU > 80% for 5 min  → SNS notify
Alarm 2: ALB 5XX errors > 10/min  → SNS notify
Alarm 3: RDS CPU > 75% for 5 min  → SNS notify
- Create SNS Topic for alerts:
Go to SNS → Create topic
Name: urbanmove-alerts
Subscription: Email → your address → confirm in inbox
Paste ARN → .env as SNS_TOPIC_ARN
STEP 12 — CloudWatch Logs (Centralized Logging)
- Go to CloudWatch → Log groups → Create log group
Name: /urbanmove/app Retention: 7 days
- On each EC2, install CloudWatch Agent:
sudo yum install amazon-cloudwatch-agent -y
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
-a fetch-config -m ec2 -c file:/opt/aws/config.json -s
- Logs to collect per instance:
## /home/ec2-user/urbanmove/logs/app.log
## /home/ec2-user/urbanmove/logs/error.log
## /var/log/messages
## 4. Create Metric Filter:
Filter pattern: ERROR
Metric name: urbanmove-error-count → add to CloudWatch dashboard
STEP 13 — Route 53 & Health Checks
(If you have a domain, use it. Otherwise use ALB DNS for demo.)
- Go to Route 53 → Create health check
Name: urbanmove-health
Endpoint: ALB DNS name

## Path: /health
Interval: 30 seconds
→ Enables automatic DR failover (configured in Step 14)
STEP 14 — DR Region Setup (us-west-2)
- Switch AWS console to us-west-2
- Create DR VPC:
Name: urbanmove-dr-vpc
## CIDR: 10.1.0.0/16
1 public subnet: 10.1.1.0/24
Internet Gateway + Route Table (same steps as Step 1)
- Launch 1 EC2 in us-west-2 (standby mode):
Same setup as primary app servers, but stop the service:
sudo systemctl stop urbanmove
This instance only activates if us-east-1 fails.
- Create RDS Read Replica:
Go to us-east-1 RDS → urbanmove-db → Actions → Create Read Replica
Destination region: us-west-2
Identifier: urbanmove-db-replica | Instance: db.t3.micro
## 5. Route 53 Failover Records:
Primary:   ALB us-east-1 DNS       (Failover → PRIMARY)
Secondary: EC2 us-west-2 public IP  (Failover → SECONDARY)
Linked to urbanmove-health check → auto-switches if primary fails
STEP 15 — Data Generator / Mobility Simulator
(Run locally or on a separate EC2 to simulate real IoT data)
- Install dependencies:
pip install kafka-python faker
- Run the GPS simulator (100 vehicles/sec):
python3 simulator/gps_generator.py \
--brokers $KAFKA_BROKERS \
--topic urbanmove.gps.events \
## --vehicles 100
- Run the traffic sensor simulator:
python3 simulator/sensor_generator.py \
--brokers $KAFKA_BROKERS \
--topic urbanmove.sensor.events \
## --sensors 50
- Verify data is flowing:

Go to MSK console → Monitoring → Bytes In
Should show active throughput on your topics
nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
.env FILE TEMPLATE
nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
## # Database
DB_HOST=<RDS endpoint here>
## DB_PORT=5432
DB_NAME=urbanmove
DB_USER=admin
DB_PASS=UrbanDB!Secure2025

## # Cache
REDIS_HOST=<ElastiCache endpoint here>
## REDIS_PORT=6379

## # Kafka
KAFKA_BROKERS=<MSK bootstrap servers here>

## # S3
S3_RAW_BUCKET=urbanmove-mobility-data-YOURNAME
S3_ANALYTICS_BUCKET=urbanmove-analytics-YOURNAME
AWS_REGION=us-east-1

## # Cognito
COGNITO_POOL_ID=<User Pool ID here>
COGNITO_CLIENT_ID=<App Client ID here>

## # Alerts
SNS_TOPIC_ARN=<SNS Topic ARN here>

## # App
## PORT=5000
NODE_ENV=production
JWT_SECRET=change_this_to_random_64_char_string
nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
DEMO CHECKLIST (screenshot each for slides)
nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
[ ] VPC with 4 subnets visible (2 public, 2 private)
[ ] 2 App EC2 instances running (green / healthy)
[ ] 1 Stream Processor EC2 running (private subnet)
[ ] ALB with both targets healthy
[ ] RDS PostgreSQL instance running

[ ] MSK Kafka cluster active + all topics created
[ ] ElastiCache Redis node running
[ ] S3 buckets with mobility data visible
[ ] Cognito User Pool with test users
[ ] App working: register → login → view routes
[ ] GPS simulator pushing data → Kafka topics active
[ ] Stream processor consuming and processing events
[ ] CloudWatch dashboard showing live metrics
[ ] CloudWatch Logs showing app logs
[ ] DR EC2 in us-west-2 (stopped / standby)
[ ] RDS Read Replica in us-west-2
[ ] S3 replication rule active
[ ] Route 53 health check green
[ ] /health endpoint returning {"status":"ok","region":"us-east-1"}
[ ] SNS alert received on email (trigger a test alarm)
nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
n
UrbanMove Platform | Cloud Computing Final Project
nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn
n