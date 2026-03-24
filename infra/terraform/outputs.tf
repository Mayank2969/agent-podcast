output "ec2_public_ip" {
  value       = aws_instance.app_server.public_ip
  description = "Public IP of the EC2 instance"
}

output "ec2_instance_id" {
  value       = aws_instance.app_server.id
  description = "ID of the EC2 instance for SSM"
}

output "rds_endpoint" {
  value       = aws_db_instance.postgres.endpoint
  description = "Endpoint of the RDS database"
}
